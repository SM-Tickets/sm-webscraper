all: clean build

clean:
	rm -rf dist build sm-webscraper.spec

build:
	# PLAYWRIGHT_BROWSERS_PATH=./browser_drivers patchright install chromium
	uv run pyinstaller --noconsole \
		--onefile \
		--add-data "./assets:./assets" \
		--collect-all apify_fingerprint_datapoints \
		--collect-all patchright \
		--collect-all playwright \
		--collect-all plyer \
		--name sm-webscraper \
		src/main.py

install:
	cp -a dist/* .

package:
	bash scripts/package.sh

.PHONY: build install clean package
