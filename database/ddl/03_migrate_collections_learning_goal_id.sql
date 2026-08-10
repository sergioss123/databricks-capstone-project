-- Add optional learning-goal ownership to collections.

ALTER TABLE collections
    ADD COLUMN IF NOT EXISTS learning_goal_id TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.table_constraints
        WHERE constraint_name = 'collections_learning_goal_id_fkey'
          AND table_name = 'collections'
    ) THEN
        ALTER TABLE collections
            ADD CONSTRAINT collections_learning_goal_id_fkey
            FOREIGN KEY (learning_goal_id) REFERENCES learning_goals(id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_collections_learning_goal_id
    ON collections (learning_goal_id);