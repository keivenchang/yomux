CREATE TABLE session (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    workspace_id TEXT,
    parent_id TEXT,
    slug TEXT NOT NULL,
    directory TEXT NOT NULL,
    path TEXT,
    title TEXT NOT NULL,
    version TEXT NOT NULL,
    share_url TEXT,
    summary_additions INTEGER,
    summary_deletions INTEGER,
    summary_files INTEGER,
    summary_diffs TEXT,
    metadata TEXT,
    cost REAL DEFAULT 0 NOT NULL,
    tokens_input INTEGER DEFAULT 0 NOT NULL,
    tokens_output INTEGER DEFAULT 0 NOT NULL,
    tokens_reasoning INTEGER DEFAULT 0 NOT NULL,
    tokens_cache_read INTEGER DEFAULT 0 NOT NULL,
    tokens_cache_write INTEGER DEFAULT 0 NOT NULL,
    revert TEXT,
    permission TEXT,
    agent TEXT,
    model TEXT,
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL,
    time_compacting INTEGER,
    time_archived INTEGER
);
CREATE TABLE message (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE part (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL,
    data TEXT NOT NULL
);
INSERT INTO session (id, project_id, slug, parent_id, directory, title, version, model, agent, time_created, time_updated)
VALUES
    ('ses-many-root', 'global', 'many-root', NULL, '/repo/many', 'many root', '1.18.30', '{"providerID":"inferencehub","id":"openai/openai/gpt-5.6-luna"}', 'build', 1788307299000, 1788307300000),
    ('ses-many-fresh', 'global', 'many-fresh', 'ses-many-root', '/repo/many', 'fresh child', '1.18.30', '{"providerID":"inferencehub","id":"openai/openai/gpt-5.6-luna"}', 'build', 1788307358000, 1788307359000);
INSERT INTO session (id, project_id, slug, parent_id, directory, title, version, model, agent, time_created, time_updated)
WITH RECURSIVE numbers(value) AS (
    SELECT 1
    UNION ALL
        SELECT value + 1 FROM numbers WHERE value < 172
)
SELECT 'ses-many-old-' || printf('%03d', value), 'global', 'old', 'ses-many-root', '/repo/many', 'historical child', '1.18.30', '{"providerID":"inferencehub","id":"openai/openai/gpt-5.6-luna"}', 'build', value, value
FROM numbers;
INSERT INTO message VALUES
    ('msg-many-root', 'ses-many-root', 1788307300000, 1788307300000, '{"role":"assistant","providerID":"inferencehub","modelID":"openai/openai/gpt-5.6-luna"}'),
     ('msg-many-fresh', 'ses-many-fresh', 1788307359000, 1788307359000, '{"role":"assistant","providerID":"inferencehub","modelID":"openai/openai/gpt-5.6-luna"}');
INSERT INTO part VALUES
    ('part-many-root', 'msg-many-root', 'ses-many-root', 1788307300000, 1788307300000, '{"snapshot":"root","type":"step-finish","tokens":{"total":10,"input":3,"output":5,"reasoning":0,"cache":{"read":2,"write":0}},"cost":0}'),
     ('part-many-fresh', 'msg-many-fresh', 'ses-many-fresh', 1788307359000, 1788307359000, '{"snapshot":"fresh","type":"step-finish","tokens":{"total":14,"input":4,"output":6,"reasoning":0,"cache":{"read":4,"write":0}},"cost":0}');
