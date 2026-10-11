# Smoke test: harvest -> geo -> images -> OCR -> export -> site build.
.PHONY: pipeline site smoke serve clean-site

pipeline:
	cd pipeline && uv run cah all

site:
	cd site && npm run build

smoke: pipeline site
	@echo "Report: data/export/report.md  Site: site/_site"

serve:
	cd site && npm run dev

clean-site:
	rm -rf site/_site
