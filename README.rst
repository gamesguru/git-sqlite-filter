git-sqlite-filter
=================

.. warning::
    **YOU CAN EASILY LOSE DATA IF YOU ISSUE WRITE COMMANDS!!!**
    To keep your data safe, only use Git operations from a user with **READ-ONLY ACCESS** to the live database file.

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

    *.sqlite filter=sqlite diff=sqlite
    *.db filter=sqlite diff=sqlite

And in your ``.gitconfig`` (global or local):

.. code-block:: ini

    [filter "sqlite"]
        clean = git-sqlite-clean %f
        smudge = git-sqlite-smudge %f
        required = true
    [diff "sqlite"]
        # Allows 'git diff' to show readable SQL changes
        textconv = git-sqlite-clean

Debugging
---------
Enable debug logging with the ``--debug`` flag or by setting ``GIT_TRACE=1``:

.. code-block:: bash

    GIT_TRACE=1 git diff mydb.sqlite

Development
-----------
Run the test suite with coverage:

.. code-block:: bash

    make dev-deps
    make test

Run linting:

.. code-block:: bash

    make lint
