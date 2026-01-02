git-sqlite-filter
=================

A Git clean/smudge filter for SQLite databases that ensures logical, deterministic SQL dumps.

Features
--------
*   **Noise Reduction**: Stable row sorting by Primary Key prevents "phantom diffs".
*   **FTS5 Support**: Correctly filters virtual table shadow tables for transparent restoration.
*   **Generated Columns**: Excludes virtual/stored columns from INSERT statements.
*   **Lock Resilience**: Uses atomic backups to handle busy databases during commits.

Installation
------------
Install directly via pip:

.. code-block:: bash

    pip install git-sqlite-filter

Usage
-----
Configure the filter in your ``.gitattributes``:

.. code-block:: text

    *.sqlite filter=sqlite

And in your ``.gitconfig``:

.. code-block:: ini

    [filter "sqlite"]
        clean = git-sqlite-clean %f
        smudge = git-sqlite-smudge
        required = true

Development
-----------
Run the test suite:

.. code-block:: bash

    ./test/run_tests.sh
