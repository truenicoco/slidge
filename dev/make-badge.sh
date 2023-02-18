#!/bin/sh

apt-get update >/dev/null 2>&1

VER=$(apt-cache policy slidge | grep "$1" -B1 | head -n1 | awk '{print $1;}')

python3 -m pybadges --left-text="debian $1" --right-text="$VER" --right-color=green
