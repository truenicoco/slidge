import asyncio
import json
import zlib

from maufbapi import AndroidMQTT as AndroidMQTTOriginal
from maufbapi.mqtt.subscription import RealtimeTopic
from maufbapi.thrift import ThriftObject

REQUEST_TIMEOUT = 60


class AndroidMQTT(AndroidMQTTOriginal):
    # TODO: remove publish() and request() on maufbapi next release
    #       since our PR has been merged
    def publish(
        self,
        topic,
        payload,
        prefix: bytes = b"",
        compress: bool = True,
    ) -> asyncio.Future:
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        if isinstance(payload, ThriftObject):
            payload = payload.to_thrift()
        if compress:
            payload = zlib.compress(prefix + payload, level=9)
        elif prefix:
            payload = prefix + payload
        info = self._client.publish(
            topic.encoded if isinstance(topic, RealtimeTopic) else topic, payload, qos=1
        )
        fut = self._loop.create_future()
        timeout_handle = self._loop.call_later(REQUEST_TIMEOUT, self._cancel_later, fut)
        fut.add_done_callback(lambda _: timeout_handle.cancel())
        self._publish_waiters[info.mid] = fut
        return fut

    async def request(
        self,
        topic: RealtimeTopic,
        response: RealtimeTopic,
        payload,
        prefix: bytes = b"",
    ):
        async with self._response_waiter_locks[response]:
            fut = self._loop.create_future()
            self._response_waiters[response] = fut
            await self.publish(topic, payload, prefix)
            timeout_handle = self._loop.call_later(
                REQUEST_TIMEOUT, self._cancel_later, fut
            )
            fut.add_done_callback(lambda _: timeout_handle.cancel())
            return await fut

    async def _dispatch(self, evt) -> None:
        # by default, AndroidMQTT logs any exceptions here, but we actually
        # want to let it propagate
        for handler in self._event_handlers[type(evt)]:
            self.log.trace("Dispatching event %s", evt)
            await handler(evt)
