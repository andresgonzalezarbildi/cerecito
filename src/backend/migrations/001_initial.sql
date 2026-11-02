CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY, applied TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS companies(rut TEXT PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS catalog(company TEXT, kind TEXT, code TEXT, data TEXT, version TEXT,
    PRIMARY KEY(company,kind,code));
CREATE TABLE IF NOT EXISTS invoices(company TEXT, key TEXT, source TEXT, decision TEXT DEFAULT '{}',
    PRIMARY KEY(company,key));
CREATE TABLE IF NOT EXISTS original_lines(company TEXT,key TEXT,position INTEGER,data TEXT,
    PRIMARY KEY(company,key,position));
CREATE TABLE IF NOT EXISTS rules(company TEXT,supplier TEXT,description TEXT,vat TEXT,data TEXT,
    PRIMARY KEY(company,supplier,description,vat));
CREATE TABLE IF NOT EXISTS proposals(company TEXT,key TEXT,position INTEGER,data TEXT,
    PRIMARY KEY(company,key,position));
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,company TEXT,status TEXT,data TEXT,created TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_company ON jobs(company)
    WHERE status IN ('queued','running','cancelling');
CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY,job TEXT,key TEXT,data TEXT,created TEXT);
INSERT OR IGNORE INTO migrations VALUES(1,datetime('now'));
