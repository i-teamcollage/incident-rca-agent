PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
export PYTHONPATH := src

.PHONY: setup data data-check demo accept live record practice test lint readme check

setup:       ## create .venv with the test tools (the agent itself needs no packages)
	python3 -m venv .venv && .venv/bin/pip install -q -e ".[dev]"

data:        ## regenerate the made-up platform and incidents
	$(PYTHON) data/generate.py

data-check:  ## confirm the committed incidents match the generator
	$(PYTHON) data/generate.py --check

demo:        ## three incidents from the stored run, no model needed
	$(PYTHON) -m incident_rca_agent demo

accept:      ## the acceptance test on the stored run, no model needed
	$(PYTHON) -m incident_rca_agent accept

live:        ## the acceptance test live on Ollama, without storing the run
	$(PYTHON) -m incident_rca_agent accept --live

record:      ## run live on Ollama and store the run
	$(PYTHON) -m incident_rca_agent accept --record

practice:    ## live run on the practice split, for tuning
	$(PYTHON) -m incident_rca_agent accept --split practice --live

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check .

readme:      ## refresh the results table in README.md from reports/
	$(PYTHON) -m incident_rca_agent readme

check: lint test data-check accept  ## everything GitHub runs on each change
	$(PYTHON) -m incident_rca_agent readme --check
