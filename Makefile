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
	ruff check --fix --quiet --exit-zero src/git_sqlite_filter test

.PHONY: lint
lint: ##H @General Run ruff lint
	ruff check src/git_sqlite_filter test
	pylint src/git_sqlite_filter test
	mypy src/git_sqlite_filter test

.PHONY: test
test: ##H @General Run tests w/ coverage report
	python3 -m pytest -v --cov=src/git_sqlite_filter --cov-report=term-missing test/

.PHONY: install
install: ##H @General Install the package locally in editable mode
	pip install -e .

.PHONY: build
build:	##H @General Build the python package (wheel/sdist)
	pip install -U build
	python3 -m build

.PHONY: clean
clean: ##H @General Remove build artifacts
	rm -rf dist/ build/ *.egg-info/ src/*.egg-info/ .pytest_cache/ .mypy_cache/ .ruff_cache/ debian/
	rm -rf packaging/arch/{src,pkg} packaging/arch/*.tar.gz packaging/arch/*.pkg.tar.zst
	find . -type d -name "__pycache__" -exec rm -rf {} +

.PHONY: dev-deps
dev-deps: ##H @General Install development dependencies
	pip install -r requirements-dev.txt

.PHONY: arch
arch:	##H @Packaging Build Arch Linux package (requires makepkg)
	cd packaging/arch && makepkg -s

.PHONY: deb
deb:	##H @Packaging Build Debian package (requires dpkg-buildpackage)
	cp -r packaging/debian debian
	dpkg-buildpackage -us -uc -b
	rm -rf debian

.PHONY: rpm
rpm:	##H @Packaging Build RPM package (requires rpmbuild)
	rpmbuild -ba packaging/rpm/git-sqlite-filter.spec

.PHONY: release
release: ##H @Release Create a GitHub release (requires gh)
	@VERSION=$$(grep -m 1 "version =" pyproject.toml | cut -d '"' -f 2); \
	gh release create "v$$VERSION" dist/* --generate-notes --title "v$$VERSION"


.PHONY: publish
publish: ##H @Release Upload to PyPI (requires twine)
	twine upload dist/*

# Version bumping helpers
.PHONY: bump-patch
bump-patch:
	@perl -pi -e 's/version = "(\d+)\.(\d+)\.(\d+)"/ "version = \"$$1.$$2." . ($$3+1) . "\""/e' pyproject.toml
	@echo "Bumped patch version"

.PHONY: bump-minor
bump-minor:
	@perl -pi -e 's/version = "(\d+)\.(\d+)\.(\d+)"/ "version = \"$$1." . ($$2+1) . ".0\""/e' pyproject.toml
	@echo "Bumped minor version"

.PHONY: bump-major
bump-major:
	@perl -pi -e 's/version = "(\d+)\.(\d+)\.(\d+)"/ "version = \"" . ($$1+1) . ".0.0\""/e' pyproject.toml
	@echo "Bumped major version"

# Combined release flow
.PHONY: deploy
deploy: clean build release publish ##H @Release Build and deploy current version to GitHub and PyPI

.PHONY: release-patch
release-patch: bump-patch ##H @Release Bump patch version, commit, tag, and deploy
	@VERSION=$$(grep -m 1 "version =" pyproject.toml | cut -d '"' -f 2); \
	git commit -a -m "Release v$$VERSION"; \
	echo "Deploying v$$VERSION..."; \
	$(MAKE) deploy

.PHONY: release-minor
release-minor: bump-minor ##H @Release Bump minor version, commit, tag, and deploy
	@VERSION=$$(grep -m 1 "version =" pyproject.toml | cut -d '"' -f 2); \
	git commit -a -m "Release v$$VERSION"; \
	echo "Deploying v$$VERSION..."; \
	$(MAKE) deploy

.PHONY: release-major
release-major: bump-major ##H @Release Bump major version, commit, tag, and deploy
	@VERSION=$$(grep -m 1 "version =" pyproject.toml | cut -d '"' -f 2); \
	git commit -a -m "Release v$$VERSION"; \
	echo "Deploying v$$VERSION..."; \
	$(MAKE) deploy

