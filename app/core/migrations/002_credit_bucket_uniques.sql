CREATE UNIQUE INDEX IF NOT EXISTS idx_credit_buckets_user_bucket_type
ON credit_buckets(user_id, bucket_type);
