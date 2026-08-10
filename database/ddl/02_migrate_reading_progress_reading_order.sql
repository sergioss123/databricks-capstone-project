-- Migration for existing databases that created reading_progress before
-- reading_order and learning_goal_id were added.

ALTER TABLE reading_progress
    ADD COLUMN IF NOT EXISTS learning_goal_id TEXT;

ALTER TABLE reading_progress
    ADD COLUMN IF NOT EXISTS reading_order INTEGER;

ALTER TABLE reading_progress
    DROP CONSTRAINT IF EXISTS reading_progress_learning_goal_id_fkey;

ALTER TABLE reading_progress
    ADD CONSTRAINT reading_progress_learning_goal_id_fkey
    FOREIGN KEY (learning_goal_id) REFERENCES learning_goals(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_reading_progress_learning_goal_id
    ON reading_progress (learning_goal_id);

CREATE INDEX IF NOT EXISTS idx_reading_progress_reading_order
    ON reading_progress (reading_order);

WITH ordered AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY learning_goal_id
            ORDER BY created_at ASC, id ASC
        ) AS computed_reading_order
    FROM reading_progress
    WHERE reading_order IS NULL AND learning_goal_id IS NOT NULL
)
UPDATE reading_progress rp
SET reading_order = ordered.computed_reading_order
FROM ordered
WHERE rp.id = ordered.id;