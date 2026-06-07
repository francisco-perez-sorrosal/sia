# Sample Task Descriptions

This document shows representative natural-language questions from the multi-DB
NL->SQL task, drawn from the **Spider** benchmark (dev split, hard/extra
difficulty) across six complex databases. It is shown to the meta-agent (to seed
the Gen-1 target agent) and to the feedback-agent (to reason about where the
target agent is failing and what to improve).

The target agent answers each question by reading its `db_id`, generating one
SQLite query, and writing it to `responses.json` as
`{"question_id": "...", "sql": "..."}`. The grader routes each query to its
`data/public/<db_id>.sqlite` database (read-only) and compares result sets as a
sorted multiset. The gold SQL is held out in `data/private/gold.json`.

Each question carries a `db_id`; the six databases differ in schema, so the agent
must answer against the named database. Bare table names come from that database's
`sqlite_master`; the weak baseline gets nothing more.

---

## The databases (and what makes them hard)

- **concert_singer** (4 tables) — stadiums, singers, concerts; aggregation +
  ordering + max-by-group.
- **dog_kennels** (8 tables) — owners, dogs, professionals, treatments; multi-join
  with OR conditions and counting.
- **student_transcripts_tracking** (11 tables) — students, courses, departments,
  enrollments; deep joins and grouping.
- **car_1** (6 tables) — continents, countries, makers, car models; scalar
  subqueries against averages.
- **world_1** (4 tables) — countries, cities, languages; population/percentage
  reasoning and set logic.
- **cre_Doc_Template_Mgt** (4 tables) — documents, templates, paragraphs; usage
  counting and template/document joins.

---

## Representative questions

**cs_01 (concert_singer):** Show the stadium name and capacity with most number of
concerts in year 2014 or after. *(aggregate + order-by-count + limit)*

**dog_01 (dog_kennels):** Which professionals live in the state of Indiana or have
done treatment on more than 2 treatments? List his or her id, last name and cell
phone. *(OR across a join + HAVING-style count)*

**stt_01 (student_transcripts_tracking):** Which department offers the most number
of degrees? List department name and id. *(join + group-by + arg-max)*

**car_01 (car_1):** Find the model of the car whose weight is below the average
weight. *(scalar subquery against an aggregate)*

**wld_01 (world_1):** Which language is the most popular in Aruba? *(join +
order-by-percentage + limit)*

**doc_01 (cre_Doc_Template_Mgt):** What is the id and type code for the template
used by the most documents? *(join + group-by + arg-max)*

Many questions also use **nested/correlated subqueries** and **set operations**
(`INTERSECT` / `EXCEPT` / `UNION`) — exactly the constructs a weak baseline (table
names only, no columns, no foreign keys, no examples, no SQL self-repair) gets
wrong or that raise execution errors.

---

## Notes

- **Why Spider hard/extra:** the questions are drawn only from Spider's `hard` and
  `extra` hardness tiers (computed with the canonical Spider hardness function).
  The Gen-1 scaffold is deliberately weak (question + bare table names only, no
  DDL, no few-shot, no retry/repair), so it answers only a fraction correctly and
  produces execution errors on the harder set-op and multi-join questions —
  leaving large headroom for the self-improving loop to climb. Adding per-`db_id`
  schema awareness, worked examples, and error-driven SQL self-repair is exactly
  what recovers that accuracy across generations.
- **Read-only:** every database is opened read-only by both the agent (if it
  inspects schema) and the grader; queries can never mutate data.
</content>
