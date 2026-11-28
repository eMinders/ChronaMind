-- Bootstrap only a disposable compatibility database, never the runtime role.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS btree_gist;
