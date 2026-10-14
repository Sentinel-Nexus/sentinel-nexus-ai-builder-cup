-- Datasets in location US. Idempotent.
CREATE SCHEMA IF NOT EXISTS `__P__.risk_raw` OPTIONS (location = 'US');
CREATE SCHEMA IF NOT EXISTS `__P__.risk_curated` OPTIONS (location = 'US');
CREATE SCHEMA IF NOT EXISTS `__P__.risk_graph` OPTIONS (location = 'US');
CREATE SCHEMA IF NOT EXISTS `__P__.eval` OPTIONS (location = 'US');
CREATE SCHEMA IF NOT EXISTS `__P__.audit` OPTIONS (location = 'US');
