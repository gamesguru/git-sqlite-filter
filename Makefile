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
			for (i = 1; i <= NR; i++) { \
				if (list[i]) printf "  \033[1;34m%-*s\033[0m  %s\n", max, targets[i], docs[i]; \
			} \
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
	black git-sqlite/
	isort git-sqlite/
