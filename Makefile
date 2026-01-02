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
			if (length(target) > max) max = length(target); \
			targets[NR] = target; docs[NR] = doc; list[NR] = 1; \
		} \
		END { \
			print "\n\033[1;36mCore Commands:\033[0m"; \
			for (i = 1; i <= NR; i++) { \
				if (list[i]) printf "  \033[1;34m%-*s\033[0m  %s\n", max, targets[i], docs[i]; \
			} \
			print "\n\033[1;36mPackaging Commands:\033[0m"; \
			printf "  \033[1;34m%-*s\033[0m  %s\n", max, "arch", "Build Arch Linux package (requires makepkg)"; \
			printf "  \033[1;34m%-*s\033[0m  %s\n", max, "deb", "Build Debian package (requires dpkg-buildpackage)"; \
			printf "  \033[1;34m%-*s\033[0m  %s\n", max, "rpm", "Build RPM package (requires rpmbuild)"; \
			print ""; \
		}' $(MAKEFILE_LIST)

.PHONY: vars
vars:	##H Debug: Print project variables
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
format:	##H Run black & isort
	black src/git_sqlite_filter/
	isort src/git_sqlite_filter/
	black test/*.py
	isort test/*.py

.PHONY: lint
lint: ##H Run ruff lint
	ruff check src/git_sqlite_filter test

.PHONY: arch
arch:	##H Build Arch Linux package
	cd packaging/arch && makepkg -s

.PHONY: deb
deb:	##H Build Debian package
	ln -sf packaging/debian debian
	dpkg-buildpackage -us -uc -b
	rm -rf debian

.PHONY: rpm
rpm:	##H Build RPM package
	rpmbuild -ba packaging/rpm/git-sqlite-filter.spec

.PHONY: test
test:	##H Run the test suite
	./test/run_tests.sh

.PHONY: install
install: ##H Install the package locally in editable mode
	pip install -e .

.PHONY: build
build:	##H Build the python package (wheel/sdist)
	pip install -U build && python3 -m build

.PHONY: dev-deps
dev-deps: ##H Install development dependencies
	pip install black isort build wheel
