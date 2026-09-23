-- Runs automatically on first Postgres container init (mounted at
-- /docker-entrypoint-initdb.d/). Creates one database per service per
-- ADR-0006 (database-per-service). flowguard_control is owned by the agent
-- subsystem (Monitor, Healer, Fraud Agent) rather than any one service.
-- Only runs on a fresh volume: an existing dev volume needs
-- `docker compose down -v`, or `CREATE DATABASE flowguard_control;` by hand.
CREATE DATABASE flowguard_user;
CREATE DATABASE flowguard_payment;
CREATE DATABASE flowguard_fraud;
CREATE DATABASE flowguard_notification;
CREATE DATABASE flowguard_control;
