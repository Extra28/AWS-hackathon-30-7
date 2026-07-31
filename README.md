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
    * P0 - 65%
    * P1 - 25%
    * P2 - 10%
* If ATM has not been refilled in 14 days, 15th day must have a refill.
ATM Details:
* each atm has 4 cartridges, holding either 10, 50 or 100 dollar notes
* atm capacity is max total cash amt when all 4 cartridges are full
* 1 cartridge has about 2000 pieces
* considered low on cash when falls below 25% of capacity (threshold)
* 