-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Grant permissions
GRANT ALL PRIVILEGES ON DATABASE castor TO castor;
