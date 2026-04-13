.PHONY: run install test clean reindex

install:
	pip install -r requirements.txt

run:
	python3 run.py

test:
	python3 test_gui.py
	python3 test_management.py

reindex:
	python3 indexer.py full

clean:
	rm -rf data/ __pycache__/ screenshots/
