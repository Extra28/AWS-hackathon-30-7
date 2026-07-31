# AWS-hackathon-30
Our kiro created app for UOB challenge statement.


# Challenge Statement:
Build a cash replenishment trip planner using ML/AI tech that produces cash replenishment schedules that are just-in-time
Success Criteria:
* Cash replenishment process is streamlined to improve productivity
* Decrease in number of trips being planned
* ATMS should not run out of cash
Business Constraints:
* Less than 190 trips per day across all ATMs
* ATMs cannot be replenished more than once a day
* Priority  mix requirement:
    * P0 (0000h to 0800h) - 65%
    * P1 (0800h to 1200h) - 25%
    * P2 (1200h to 0000h) - 10%
* If ATM has not been refilled in 14 days, 15th day must have a refill.
ATM Details:
* each atm has 4 cartridges, holding either 10, 50 or 100 dollar notes
* atm capacity is max total cash amt when all 4 cartridges are full
* 1 cartridge has about 2000 pieces
* considered low on cash when falls below 25% of capacity (threshold)
* 350 ATMS total

optimiser formula:
minimize  w1·(trips) + w2·(idle cash × days) + w3·(stockout risk)

run in vscode terminal:
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

.\.venv\Scripts\python.exe run_pipeline.py

.\.venv\Scripts\python.exe app.py
