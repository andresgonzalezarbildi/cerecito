CREATE TABLE IF NOT EXISTS fiscal_identities(
    company TEXT NOT NULL, supplier TEXT NOT NULL, rut TEXT NOT NULL,
    evidence TEXT NOT NULL, confirmed TEXT NOT NULL,
    PRIMARY KEY(company,supplier)
);
CREATE VIEW IF NOT EXISTS suppliers AS SELECT * FROM catalog WHERE kind='supplier';
CREATE VIEW IF NOT EXISTS articles AS SELECT * FROM catalog WHERE kind='article';
CREATE VIEW IF NOT EXISTS concepts AS SELECT * FROM catalog WHERE kind='concept';
CREATE VIEW IF NOT EXISTS vat_rates AS SELECT * FROM catalog WHERE kind='vat';
CREATE VIEW IF NOT EXISTS cost_centers AS SELECT * FROM catalog WHERE kind='center';
CREATE VIEW IF NOT EXISTS payments AS SELECT * FROM catalog WHERE kind='payment';
CREATE VIEW IF NOT EXISTS invoice_decisions AS SELECT company,key,decision FROM invoices;
