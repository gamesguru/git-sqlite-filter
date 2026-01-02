*******************
 git-sqlite-filter
*******************

Supports sqlite v3.38.0+ (for WAL mode).


Configuration (User-level vs. Repository-level)
###############################################

Add to your ``~/.gitconfig`` file:

.. code-block:: config

  [filter "sqlite"]
      clean = git-sqlite-clean %f
      smudge = "sqlite3 -init /dev/null -batch %f .read"
      required = true
 [diff "sqlite"]
      # Allows 'git diff' to show readable SQL changes for binary files
      textconv = "sqlite3 \"$1\" .dump"
