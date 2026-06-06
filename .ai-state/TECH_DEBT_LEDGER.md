# Tech Debt Ledger

Living, append-only ledger of grounded debt findings. Producers (verifier, sentinel) append rows; consumers update `status` in place; rows are never deleted. Schema and producer/consumer contracts live in the agent intermediate documents rule.

| id | severity | class | direction | location | goal-ref-type | goal-ref-value | source | first-seen | last-seen | owner-role | status | resolved-by | notes | dedup_key |
|----|----------|-------|-----------|----------|---------------|----------------|--------|------------|-----------|------------|--------|-------------|-------|-----------|
