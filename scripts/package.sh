#!/usr/bin/env bash

required_path="$(dirname "$(dirname "$(realpath "$0")")")"

if [ "$PWD" != "$required_path" ]; then
    echo ERROR: Script must be run from "$required_path" 1>&2
    exit 1
fi

if [ ! -d ./dist ]; then
    echo "ERROR: Project is not built yet. First run \`make build\`." 1>&2
    exit 1
fi

if [ "$(uname -o)" = "Msys" ]; then
    cp dist/sm-webscraper.exe .
    zip -r dist/sm-webscraper_win_x86-64.zip sm-webscraper.exe
    rm sm-webscraper.exe
elif [ "$(uname -o)" = "Darwin" ]; then
    if [ "$(uname -m)" = "x86_64" ]; then
        true
    elif [ "$(uname -m)" = "arm64" ]; then
        cp -a dist/sm-webscraper.app scripts/macos/sanitize .
        tar czvf dist/sm-webscraper_macos_arm64.tar.gz sm-webscraper.app sanitize
        rm -rf sm-webscraper.app sanitize
    fi
elif [ "$(uname -s)" = "Linux" ]; then
    cp dist/sm-webscraper .
    tar czvf dist/sm-webscraper_linux_x86-64.tar.gz sm-webscraper
    rm sm-webscraper
fi
