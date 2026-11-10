# Synthetic test fixtures

Every document in this folder is wholly invented. The project names, contract numbers,
descriptions, and amounts exist only to exercise the parser and the billing rules; none of
them are copied or paraphrased from a real client document.

Do **not** replace these files with extracts from real pre-bill PDFs, and do not reuse real
task descriptions, contract names, or dollar amounts when adding cases — invent new ones.
Real extracts belong under the git-ignored `reports/` tree (or another local-only location),
never in a tracked file. The test suite and its fixtures are safe to commit; the monthly
PDFs and anything derived from them are not.
