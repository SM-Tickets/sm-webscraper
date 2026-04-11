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
    cp dist/axs-webscraper.exe .
    zip -r dist/axs-webscraper_win_x86-64.zip axs-webscraper.exe browser_drivers/;
    rm axs-webscraper.exe
    cp dist/axs-webscraper.exe dist/axs-webscraper-core_win_x86-64.exe
elif [ "$(uname -o)" = "Darwin" ]; then
    if [ "$(uname -m)" = "x86_64" ]; then
        cp -a dist/axs-webscraper dist/axs-webscraper.app .
        tar czvf dist/axs-webscraper_macos_x86-64.tar.gz axs-webscraper axs-webscraper.app browser_drivers/;
        rm -rf axs-webscraper axs-webscraper.app
        cp dist/axs-webscraper dist/axs-webscraper-core_macos_x86-64
        cp -a dist/axs-webscraper.app dist/axs-webscraper-core_macos_x86-64.app
    elif [ "$(uname -m)" = "arm64" ]; then
        cp -a dist/axs-webscraper dist/axs-webscraper.app scripts/macos/sanitize .
        tar czvf dist/axs-webscraper_macos_arm64.tar.gz axs-webscraper axs-webscraper.app browser_drivers/;
        rm -rf axs-webscraper axs-webscraper.app sanitize
        cp dist/axs-webscraper dist/axs-webscraper-core_macos_arm64
        cp -a dist/axs-webscraper.app dist/axs-webscraper-core_macos_arm64.app
    fi
elif [ "$(uname -s)" = "Linux" ]; then
    cp dist/axs-webscraper .
    tar czvf dist/axs-webscraper_linux_x86-64.tar.gz axs-webscraper browser_drivers/
    rm axs-webscraper
    cp dist/axs-webscraper dist/axs-webscraper-core_linux_x86-64
fi
