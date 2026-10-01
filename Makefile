# Reproducible entry points. Every target is a thin wrapper over the `taxi` CLI
# so there is exactly one implementation of each operation.
PY ?= python3
MONTHS ?= 2025-01..2025-12
LABEL ?= manual

.PHONY: install install-dev init acquire-synthetic acquire-real ingest marts \
        bench ml dashboard test lint report demo clean-data status

install:            ; $(PY) -m pip install -e .
install-dev:        ; $(PY) -m pip install -e ".[all]"
init:               ; taxi init
acquire-synthetic:  ; taxi acquire --months $(MONTHS) --synthetic
acquire-real:       ; taxi acquire --months $(MONTHS)
ingest:             ; taxi ingest --months $(MONTHS)
marts:              ; taxi marts build
bench:              ; taxi bench run --label $(LABEL)
ml:                 ; taxi ml features && taxi ml train && taxi ml evaluate
dashboard:          ; streamlit run dashboard/app.py
test:               ; pytest -q
lint:               ; $(PY) -m compileall -q src dashboard tests
report:             ; taxi report
status:             ; taxi status

# Full reproducible path on synthetic data: nothing here needs the network.
demo:
	taxi init
	taxi acquire --months 2025-01..2025-03 --synthetic --rows 250000
	taxi ingest --months 2025-01..2025-03
	taxi marts build
	taxi ml features && taxi ml train && taxi ml evaluate
	taxi bench run --label demo
	taxi report
	taxi status

clean-data:         ; rm -rf data/clean data/rejected data/marts data/features data/metadata data/_tmp
