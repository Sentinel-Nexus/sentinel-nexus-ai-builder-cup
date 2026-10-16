-- Connected components over the link graph by min-label propagation (set-based, one pass per
-- hop; stops when no label changes, 12 hops at most). ring_id names a component.
CREATE OR REPLACE TABLE `__OUT__.__PFX__det_labels` AS
SELECT account_id, account_id AS label FROM `__ACCOUNTS__`;

BEGIN
  DECLARE changed INT64 DEFAULT 1;
  DECLARE hops INT64 DEFAULT 0;
  WHILE changed > 0 AND hops < 12 DO
    CREATE OR REPLACE TABLE `__OUT__.__PFX__det_labels_next` AS
    SELECT l.account_id, LEAST(l.label, IFNULL(MIN(n.label), l.label)) AS label
    FROM `__OUT__.__PFX__det_labels` l
    LEFT JOIN `__OUT__.__PFX__det_dir` e ON e.src = l.account_id
    LEFT JOIN `__OUT__.__PFX__det_labels` n ON n.account_id = e.dst
    GROUP BY l.account_id, l.label;
    SET changed = (
      SELECT COUNT(*) FROM `__OUT__.__PFX__det_labels_next` x
      JOIN `__OUT__.__PFX__det_labels` y USING (account_id) WHERE x.label != y.label
    );
    CREATE OR REPLACE TABLE `__OUT__.__PFX__det_labels` AS
    SELECT * FROM `__OUT__.__PFX__det_labels_next`;
    SET hops = hops + 1;
  END WHILE;
  ASSERT changed = 0 AS 'component labels did not converge in 12 hops';
END;

CREATE OR REPLACE TABLE `__OUT__.__PFX__det_components` AS
WITH size AS (
  SELECT label, COUNT(*) AS comp_size FROM `__OUT__.__PFX__det_labels` GROUP BY label
),
edges AS (
  SELECT l.label, COUNT(*) AS comp_edges
  FROM (SELECT DISTINCT a, b FROM `__OUT__.__PFX__det_pairs`) p
  JOIN `__OUT__.__PFX__det_labels` l ON l.account_id = p.a
  GROUP BY l.label
)
SELECT
  l.account_id,
  CONCAT('R_', l.label) AS ring_id,
  s.comp_size,
  IFNULL(e.comp_edges, 0) AS comp_edges,
  IF(s.comp_size > 1, IFNULL(e.comp_edges, 0) / (s.comp_size * (s.comp_size - 1) / 2), 0)
    AS comp_density
FROM `__OUT__.__PFX__det_labels` l
JOIN size s USING (label)
LEFT JOIN edges e USING (label);
