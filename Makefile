BUMP ?= patch

.PHONY: help install test package release

help:
	@printf '%s\n' \
	    'make install                           Build this checkout and install it as the global s1r command' \
	    'make test                              Run the offline tests' \
	    'make package                           Build the release assets in dist-release/' \
	    'make release [BUMP=patch|minor|major]  Tag the next version on main and push it; CI publishes the release'

install: package
	SYSTEM_ONE_ROUTER_DOWNLOAD_BASE="$(CURDIR)/dist-release" sh install.sh

test:
	python3 -m unittest discover -s tests

package:
	sh scripts/package-release.sh

release:
	BUMP=$(BUMP) sh scripts/release.sh
