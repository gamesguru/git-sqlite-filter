SHELL:=/bin/bash
# .ONESHELL:
# .EXPORT_ALL_VARIABLES:
.DEFAULT_GOAL := _help
.SHELLFLAGS = -ec

.PHONY: _help
_help:
	@printf "\nUsage: make <command>, valid commands:\n\n"
	@awk 'BEGIN {FS = ":.*?##H "}; \
		/##H/ && !/@awk.*?##H/ { \
			target=$$1; doc=$$2; \
			category="General"; \
			if (doc ~ /^@/) { \
				category=substr(doc, 2, index(doc, " ")-2); \
				doc=substr(doc, index(doc, " ")+1); \
			} \
			if (length(target) > max) max = length(target); \
			targets[NR] = target; docs[NR] = doc; cats[NR] = category; \
		} \
		END { \
			last_cat = ""; \
			for (i = 1; i <= NR; i++) { \
				if (cats[i] != "") { \
					if (cats[i] != last_cat) { \
						printf "\n\033[1;36m%s Commands:\033[0m\n", cats[i]; \
						last_cat = cats[i]; \
					} \
					printf "  \033[1;34m%-*s\033[0m  %s\n", max, targets[i], docs[i]; \
				} \
			} \
			print ""; \
		}' $(MAKEFILE_LIST)

.PHONY: vars
vars:	##H @General Debug: Print project variables
	@$(foreach v,$(sort $(.VARIABLES)), \
		$(if $(filter file command line override,$(origin $(v))), \
			$(info $(v) = $($(v))) \
		) \
	)

define print_err
	printf "\033[1;31m%s\033[0m\n" "$(1)"
endef

define print_warn
	printf "\033[1;33m%s\033[0m\n" "$(1)"
endef

define print_success
	printf "\033[1;34m✓ %s\033[0m\n" "$(1)"
endef

define print_info
	printf "\033[1;36m%s\033[0m\n" "$(1)"
endef


.PHONY: format
format:	##H @General Run black & isort
	black src/git_sqlite_filter/
	isort src/git_sqlite_filter/
	black test/*.py
	isort test/*.py

.PHONY: lint
lint: ##H @General Run ruff lint
	ruff check src/git_sqlite_filter test

.PHONY: arch
arch:	##H @Packaging Build Arch Linux package (requires makepkg)
	cd packaging/arch && makepkg -s

.PHONY: deb
deb:	##H @Packaging Build Debian package (requires dpkg-buildpackage)
	ln -sf packaging/debian debian
	dpkg-buildpackage -us -uc -b
	rm -rf debian

.PHONY: rpm
rpm:	##H @Packaging Build RPM package (requires rpmbuild)
	rpmbuild -ba packaging/rpm/git-sqlite-filter.spec

.PHONY: test
test:	##H @General Run the test suite (using pytest)
	pytest -v test/test_filters.py

.PHONY: install
install: ##H @General Install the package locally in editable mode
	pip install -e .

.PHONY: build
build:	##H @General Build the python package (wheel/sdist)
	pip install -U build && python3 -m build

.PHONY: release
release: ##H @Release Tag and create a GitHub release (requires gh)
	@VERSION=$$(grep -m 1 "version =" pyproject.toml | cut -d '"' -f 2); \
	if git rev-parse "v$$VERSION" >/dev/null 2>&1; then \
		echo "Version v$$VERSION already tagged."; \
	else \
		echo "Tagging v$$VERSION..."; \
		git tag -a "v$$VERSION" -m "Release v$$VERSION"; \
		git push origin "v$$VERSION"; \
	fi; \
	echo "Creating GitHub release..."; \
	gh release create "v$$VERSION" dist/* --generate-notes

.PHONY: publish
publish: ##H @Release Upload to PyPI (requires twine)
	twine upload dist/*

.PHONY: clean
clean: ##H @General Remove build artifacts
	rm -rf dist/ build/ *.egg-info/ src/*.egg-info/ .tmp/ .pytest_cache/ .mypy_cache/ debian/
	find . -type d -name "__pycache__" -exec rm -rf {} +

.PHONY: dev-deps
dev-deps: ##H @General Install development dependencies
	pip install black isort build wheel ruff twine pytest
