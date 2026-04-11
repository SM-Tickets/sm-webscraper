all: clean build

clean:
	rm -rf dist build axs-webscraper.spec

build:
	PLAYWRIGHT_BROWSERS_PATH=./browser_drivers playwright install chromium
	pyinstaller --noconsole \
		--icon ./assets/axs_logo.png \
		--onefile \
		--add-data "./assets:./assets" \
		--name axs-webscraper \
		src/main.py

install:
	cp -a dist/* .

package:
	bash scripts/package.sh

.PHONY: build install clean package
