CREATE TABLE IF NOT EXISTS legacy_suggestions(
    company TEXT NOT NULL,
    supplier TEXT NOT NULL,
    description TEXT NOT NULL,
    vat TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY(company,supplier,description,vat)
);
