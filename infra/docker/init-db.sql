-- Runs automatically on first Postgres container init (mounted at
-- /docker-entrypoint-initdb.d/). Creates one database per service per
-- ADR-0006 (database-per-service). flowguard_control (owned by the agent
-- subsystem) is added when Phase 7 introduces the Monitor Agent.
CREATE DATABASE flowguard_user;
CREATE DATABASE flowguard_payment;
CREATE DATABASE flowguard_fraud;
CREATE DATABASE flowguard_notification;
