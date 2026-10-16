-- Datasets in location US. Idempotent.
CREATE SCHEMA IF NOT EXISTS `__P__.risk_raw` OPTIONS (location = 'US');
CREATE SCHEMA IF NOT EXISTS `__P__.risk_curated` OPTIONS (location = 'US');
CREATE SCHEMA IF NOT EXISTS `__P__.risk_graph` OPTIONS (location = 'US');
CREATE SCHEMA IF NOT EXISTS `__P__.eval` OPTIONS (location = 'US');
CREATE SCHEMA IF NOT EXISTS `__P__.audit` OPTIONS (location = 'US');
-- Validation seeds for detector threshold selection (P2). Tables are s<seed>_<name>.
CREATE SCHEMA IF NOT EXISTS `__P__.eval_val` OPTIONS (location = 'US');
