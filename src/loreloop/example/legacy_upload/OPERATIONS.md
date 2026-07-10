# Legacy upload operations

The application predates its current maintainers. The size limit appears in code,
tests, and the live status page, so a safe change must keep all three aligned.

Known drift scenario: change `MAX_UPLOAD_MIB` without updating `test_contract.py`.
The bundled task deliberately asks the coding agent to update both, then loreloop
checks the running page and feeds the accepted rule back into the knowledge store.
