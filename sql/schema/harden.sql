-- hard negatives: 3 innocent groups sharing a device AND funding source
UPDATE `__P__.risk_curated.accounts` SET device_id='DH1', funding_source_id='FH1'
WHERE account_id BETWEEN 'A0100' AND 'A0105';
UPDATE `__P__.risk_curated.accounts` SET device_id='DH2', funding_source_id='FH2'
WHERE account_id BETWEEN 'A0200' AND 'A0204';
UPDATE `__P__.risk_curated.accounts` SET device_id='DH3', funding_source_id='FH3'
WHERE account_id BETWEEN 'A0300' AND 'A0306';
-- partial links: part of each attack stops sharing funding (still linked by device or behavior)
UPDATE `__P__.risk_curated.accounts` SET funding_source_id = CONCAT('FX_', account_id)
WHERE account_id BETWEEN 'M011' AND 'M025'
   OR account_id BETWEEN 'W005' AND 'W008'
   OR account_id BETWEEN 'C004' AND 'C006';
