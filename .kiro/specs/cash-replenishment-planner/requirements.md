# Requirements Document

## Introduction

A cash replenishment trip planner for a 350-ATM fleet that uses demand forecasting to produce just-in-time refill schedules. The system consumes hourly ATM status telemetry, forecasts per-ATM cash withdrawal demand, derives a refill window for each machine, and packs those windows into daily schedules that satisfy the bank's operating constraints while minimising total trips and idle cash in the field.

The central objective is the elimination of ad hoc trips. Today, unplanned trips are dispatched reactively when an ATM drops below its low-cash threshold or reports a fault. These trips consume the same daily trip budget as planned ones, arrive outside the agreed priority windows, and are more expensive and disruptive to execute. A successful planner converts would-be ad hoc trips into planned trips scheduled in advance.

The planner must beat two incumbent policies simultaneously: a reactive policy that dispatches only once an ATM is already low, and a fixed-calendar policy that refills every N days regardless of demand.

### Scope decisions

These were resolved before drafting and are treated as settled:

- **One trip equals one ATM visit.** A day's plan is a set of ATMs, not a set of vehicle routes. Travel time, depot assignment, and crew routing are out of scope.
- **The 65/25/10 priority mix is enforced per day**, not aggregated over a week or month.
- **All data is synthesised.** No historical data is available, so the system generates its own fleet, hourly status history, and fault events.
- **ATMs report status hourly.** The system is built around hourly telemetry, not daily snapshots.
- **Ad hoc trips exist and are in scope.** They are triggered by a low-cash breach or a fault, and they consume the daily trip budget.

### The refill band

The operative scheduling rule, and the source of the planner's flexibility:

- An ATM becomes **eligible** for a planned refill once its balance falls below **55% of capacity**.
- An ATM's balance must not fall below **25% of capacity**, the low-cash threshold. This is the hard floor.
- The 30-percentage-point span between these levels is the **refill band**: the scheduling slack the optimiser uses to smooth daily workload, satisfy the priority mix, and stay within the trip cap.
- Refilling at the 55% end is safe but strands idle cash. Refilling near the 25% end is just-in-time but leaves little margin for forecast error. Positioning visits within this band is the primary trade-off the optimiser manages.
- An ATM above 55% should not normally receive a planned refill, since carting cash to a machine that does not need it wastes a trip and inflates idle cash.

### Derived facts

Load-bearing arithmetic that the design depends on:

- Cartridge capacity is 2,000 notes, so a single cartridge holds $20,000 / $100,000 / $200,000 for $10 / $50 / $100 denominations respectively.
- Fleet capacity spans a 10x range: $80,000 for an all-$10 ATM up to $800,000 for an all-$100 ATM. Every threshold, band, and fill calculation must be per-ATM; fleet-wide dollar figures are meaningless.
- Band levels are therefore also per-ATM. An all-$10 ATM has a band of $20,000 to $44,000; an all-$100 ATM has a band of $200,000 to $440,000.
- "Less than 190 trips per day" is read as a maximum of **189** trips per day, and this cap covers planned and ad hoc trips combined.
- "Not refilled in 14 days means the 15th day must have a refill" is read as a **maximum gap of 15 days** between consecutive refills.
- The 15-day maximum gap across 350 ATMs imposes a floor of ~24 trips per day on average (350 / 15 = 23.3). The feasible operating band is roughly 24 to 189 trips per day, leaving substantial headroom below the cap.
- Because the trip cap is shared with ad hoc trips, the planner cannot fill it. It must reserve headroom for expected ad hoc demand.
- Priority windows are of unequal length: P0 spans 8 hours (0000-0800), P1 spans 4 hours (0800-1200), P2 spans 12 hours (1200-0000). Hourly telemetry makes the balance at each window's start directly computable, so window assignment is a safety decision with an exact feasibility test.

---

## Requirements

### Requirement 1: Synthetic ATM fleet generation

**User Story:** As a data scientist, I want a realistic synthetic ATM fleet, so that I can develop and evaluate the planner without access to production bank data.

#### Acceptance Criteria

1. WHEN the fleet generator is run THEN the system SHALL produce exactly 350 ATM records.
2. WHEN an ATM record is generated THEN the system SHALL assign it a unique identifier, a location type, a geographic region, and a cartridge configuration.
3. WHEN a cartridge configuration is generated THEN the system SHALL assign exactly 4 cartridges, each holding a single denomination drawn from $10, $50, or $100.
4. WHEN a cartridge configuration is assigned THEN the system SHALL compute total ATM capacity as the sum over cartridges of 2,000 multiplied by the cartridge denomination.
5. WHEN an ATM's capacity is known THEN the system SHALL compute and store its refill eligibility level as 55% of capacity and its low-cash threshold as 25% of capacity.
6. WHEN location types are assigned THEN the system SHALL include a mix of at least the following profiles: shopping mall, office district, residential estate, transit hub, and tourist area.
7. WHEN a location type is assigned THEN the system SHALL correlate the ATM's cartridge denomination mix with that location type, so that high-value locations skew toward larger denominations and residential locations skew toward smaller ones.
8. WHEN an ATM record is generated THEN the system SHALL assign it a baseline fault propensity, so that reliability varies across the fleet.
9. WHEN the generator is run twice with the same random seed THEN the system SHALL produce byte-identical fleet output.

### Requirement 2: Hourly ATM status telemetry

**User Story:** As a planner, I want hourly status readings from every ATM, so that balances, depletion rates, and intraday behaviour are known at the granularity the operation actually reports.

#### Acceptance Criteria

1. WHEN status history is generated THEN the system SHALL produce one status record per ATM per hour.
2. WHEN a status record is generated THEN the system SHALL include the timestamp, the balance per cartridge, the total balance, the balance as a percentage of capacity, and the operational state of the machine.
3. WHEN a status record is generated THEN the system SHALL classify the balance against the refill band as above eligibility, within the band, or below the low-cash threshold.
4. WHEN a status record is generated THEN the system SHALL record which priority window the hour falls within.
5. WHEN status history is stored THEN the system SHALL use a columnar format capable of handling the full volume, being 350 ATMs times 24 hours times the history length.
6. WHEN the planner consumes telemetry THEN the system SHALL treat the most recent status record per ATM as that ATM's current state.
7. IF a status record is missing or an ATM fails to report THEN the system SHALL handle the gap explicitly rather than assuming the last known balance is current.
8. WHEN the generator is run twice with the same random seed THEN the system SHALL produce byte-identical status output.

### Requirement 3: Synthetic withdrawal demand generation

**User Story:** As a data scientist, I want synthetic withdrawal demand with realistic seasonality at hourly granularity, so that a forecasting model has genuine signal and the evaluation is not trivially easy.

#### Acceptance Criteria

1. WHEN the demand generator is run THEN the system SHALL produce at least 24 months of history, so that at least two Chinese New Year periods are covered and sufficient data exists for both training and walk-forward backtesting.
2. WHEN demand is generated THEN the system SHALL generate it at hourly granularity and SHALL record withdrawal volume broken down by denomination.
3. WHEN generating demand THEN the system SHALL apply an hour-of-day profile that varies by location type, so that an office district ATM peaks at different hours than a residential estate ATM.
4. WHEN generating demand THEN the system SHALL apply day-of-week seasonality.
5. WHEN generating demand THEN the system SHALL apply monthly payday effects, with elevated demand around end-of-month and mid-month salary credit dates.
6. WHEN generating demand THEN the system SHALL apply Singapore public holiday effects, including a pronounced Chinese New Year spike reflecting demand for cash gifting.
7. WHEN generating demand THEN the system SHALL scale base demand by location type, so that ATMs differ materially in both average draw and volatility.
8. WHEN generating demand THEN the system SHALL add stochastic noise and occasional demand shocks, so that the series is not perfectly predictable.
9. WHEN a demand value is realised THEN the system SHALL constrain it to be non-negative and SHALL NOT allow withdrawals exceeding the cash physically available in the relevant cartridges.
10. IF demand is suppressed because cash was unavailable THEN the system SHALL record the unmet demand separately, so that service failure is measurable and is not hidden as reduced demand.
11. WHEN the generator is run twice with the same random seed THEN the system SHALL produce byte-identical demand output.

### Requirement 4: Fault and ad hoc event generation

**User Story:** As a data scientist, I want synthetic fault events, so that the planner and simulation are tested against the unplanned trips that occur in real operations.

#### Acceptance Criteria

1. WHEN fault events are generated THEN the system SHALL emit them as a stochastic process independent of cash level, since a machine can fail while well stocked.
2. WHEN a fault event is generated THEN the system SHALL assign it a fault category, at minimum distinguishing faults that disable dispensing from faults that degrade service without disabling it.
3. WHEN a fault event is generated THEN the system SHALL use the ATM's baseline fault propensity from Requirement 1, so that some machines fail more often than others.
4. WHEN a fault disables dispensing THEN the system SHALL suppress demand at that ATM for the duration of the outage and SHALL record the demand as unmet.
5. WHEN a fault occurs THEN the system SHALL record its detection time, on the basis that faults become visible through the hourly status feed rather than instantly.
6. WHEN the generator is run twice with the same random seed THEN the system SHALL produce byte-identical fault output.

### Requirement 5: Demand forecasting with uncertainty

**User Story:** As a planner, I want probabilistic demand forecasts rather than single point estimates, so that refill timing can be set against a confidence level instead of an average case.

#### Acceptance Criteria

1. WHEN a forecast is requested for an ATM THEN the system SHALL produce a daily demand forecast for a horizon of at least 21 days, which exceeds the 15-day maximum refill gap.
2. WHEN a forecast is produced THEN the system SHALL emit quantile estimates including at minimum the 50th, 90th, and 95th percentiles.
3. WHEN a forecast is produced THEN the system SHALL forecast demand per denomination as well as in aggregate.
4. WHEN a forecast is produced THEN the system SHALL also produce an intraday allocation of each forecast day's demand across the P0, P1, and P2 windows, derived from the ATM's observed hourly pattern.
5. WHEN the forecasting model is trained THEN the system SHALL derive features from calendar effects, lagged demand, and rolling demand statistics.
6. WHEN the forecasting model is trained THEN the system SHALL exclude or correct periods of suppressed demand caused by cash unavailability or disabling faults, so that the model does not learn service failures as genuine demand.
7. WHEN the forecasting model is evaluated THEN the system SHALL use walk-forward validation on held-out time periods and SHALL NOT evaluate on data used for training.
8. WHEN forecast accuracy is reported THEN the system SHALL report point-forecast error metrics and quantile calibration metrics, including pinball loss and empirical coverage of the predicted intervals.
9. WHEN the model is benchmarked THEN the system SHALL compare it against a seasonal-naive baseline and SHALL report whether it improves on that baseline.
10. IF an ATM has insufficient history to train on THEN the system SHALL fall back to a cohort-level forecast based on comparable ATMs rather than failing.

### Requirement 6: Refill window computation

**User Story:** As a planner, I want each ATM to carry an earliest and latest refill date, so that scheduling has explicit slack to work with rather than a single fixed deadline.

#### Acceptance Criteria

1. WHEN an ATM's current balance and demand forecast are known THEN the system SHALL compute the projected date on which its balance falls below 55% of capacity, and SHALL treat this as the opening of its refill window.
2. WHEN an ATM's current balance and demand forecast are known THEN the system SHALL compute the projected date on which its balance falls below 25% of capacity, and SHALL treat this as the closing of its refill window.
3. WHEN computing window boundaries THEN the system SHALL use a configurable high demand quantile rather than the median, so that the window reflects a safety margin.
4. WHEN computing a refill window THEN the system SHALL also compute the projected date of full depletion, and SHALL distinguish this from the low-cash threshold date.
5. WHEN computing a refill window THEN the system SHALL evaluate depletion per cartridge as well as in aggregate, so that an ATM unable to dispense one denomination is detected even while holding cash in others.
6. WHEN an ATM's last refill date is known THEN the system SHALL compute its regulatory deadline as 15 days after that date.
7. WHEN both a demand-driven window close and a regulatory deadline exist THEN the system SHALL set the effective latest refill date to the earlier of the two.
8. WHEN a refill window is computed THEN the system SHALL record which driver determined its closing date, distinguishing demand-driven from compliance-driven.
9. IF an ATM's regulatory deadline falls while it is still projected to be above 55% of capacity THEN the system SHALL flag the resulting visit as a compliance-driven low-value trip, so that the operational cost of the 14-day rule is visible and quantifiable.
10. WHEN window boundaries are computed THEN the system SHALL resolve them to the hour, so that a visit's priority window can be tested against them.

### Requirement 7: Daily schedule generation under hard constraints

**User Story:** As an operations manager, I want a daily schedule that never breaches our operating constraints, so that the plan can be executed as issued without manual correction.

#### Acceptance Criteria

1. WHEN a schedule is generated for any single day THEN the system SHALL ensure planned trips plus reserved ad hoc capacity does not exceed 189 visits on that day.
2. WHEN a schedule is generated THEN the system SHALL reserve daily headroom for expected ad hoc trips, sized from forecast fault rates and residual low-cash risk, so that an ad hoc trip does not push the day over the cap.
3. WHEN a schedule is generated for any single day THEN the system SHALL schedule each ATM at most once on that day.
4. WHEN a schedule is generated across a planning horizon THEN the system SHALL ensure no ATM goes more than 15 days between consecutive refills.
5. WHEN an ATM is scheduled for a planned refill THEN the system SHALL place the visit on or after its refill window opening date and on or before its effective latest refill date.
6. WHEN an ATM is projected to be above 55% of capacity at the proposed visit time THEN the system SHALL NOT schedule a planned refill, unless the visit is required to satisfy the 15-day rule.
7. IF the 15-day rule forces a visit to an ATM projected to be above 55% of capacity THEN the system SHALL schedule the visit and SHALL record it as compliance-driven.
8. WHEN a schedule is generated THEN the system SHALL plan over a rolling multi-day horizon of at least 21 days rather than optimising each day in isolation, so that window clustering is anticipated and smoothed.
9. WHEN the optimiser runs THEN the system SHALL minimise a weighted objective combining planned trip count, expected ad hoc trip count, idle cash held in the field over time, and low-cash breach risk.
10. WHEN the objective is evaluated THEN the system SHALL weight ad hoc trips more heavily than planned trips, reflecting their higher execution cost and disruption.
11. WHEN the objective weights are changed THEN the system SHALL produce correspondingly different schedules, so that the trade-off between trip count and working capital can be explored.
12. IF the set of ATMs requiring a refill on a given day cannot be accommodated without breaching a hard constraint THEN the system SHALL report the infeasibility explicitly and SHALL NOT silently omit any ATM from the plan.
13. WHEN the optimiser is given identical inputs and configuration THEN the system SHALL produce an identical schedule.
14. WHEN a schedule is generated for the full 350-ATM fleet over the planning horizon THEN the system SHALL complete within a configured runtime budget, and the budget SHALL be low enough to support interactive plan regeneration from the dashboard.

### Requirement 8: Priority window assignment

**User Story:** As an operations manager, I want each scheduled visit assigned to a priority window in the required daily proportions, so that crew shift loading matches our agreed operating pattern and the most urgent ATMs are served first.

#### Acceptance Criteria

1. WHEN a day's planned visit count is determined THEN the system SHALL apportion planned visits across P0 at 65%, P1 at 25%, and P2 at 10%.
2. WHEN the apportionment produces fractional targets THEN the system SHALL resolve them using largest-remainder apportionment, so that the three band counts sum exactly to the day's planned visit count.
3. WHEN visits are assigned to windows THEN the system SHALL assign the most urgent ATMs to P0, on the basis that P0 runs 0000-0800 and therefore refills before the bulk of the day's demand.
4. WHEN an ATM is assigned to P1 or P2 THEN the system SHALL verify using the forecast intraday allocation that its projected balance stays above the 25% threshold until that window opens.
5. IF an ATM cannot survive until its assigned window opens THEN the system SHALL move it to an earlier window.
6. IF more ATMs require P0 placement than the P0 quota allows THEN the system SHALL increase the day's planned visit count so that the proportionally derived P0 quota accommodates them, rather than breaching the priority mix.
7. WHEN the day's planned visit count is increased to widen the P0 quota THEN the system SHALL respect the daily cap net of reserved ad hoc capacity, and SHALL fill the resulting additional P1 and P2 slots with the next most urgent eligible ATMs.
8. IF the number of ATMs requiring P0 placement exceeds the P0 quota available at the effective cap THEN the system SHALL report this as an infeasibility.
9. WHEN the priority mix is reported THEN the system SHALL report the planned mix and the realised mix including ad hoc trips separately, so that drift caused by unplanned trips is visible.

### Requirement 9: Ad hoc trip handling

**User Story:** As an operations manager, I want ad hoc trips modelled and accounted for, so that unplanned work is visible in the plan rather than absorbed invisibly by the crews.

#### Acceptance Criteria

1. WHEN an ATM's balance falls below 25% of capacity without a planned refill scheduled in time THEN the system SHALL raise an ad hoc cash trip.
2. WHEN an ATM reports a fault that disables dispensing THEN the system SHALL raise an ad hoc trip independently of its cash level.
3. WHEN an ad hoc trip is raised THEN the system SHALL count it against the same daily cap of 189 trips that governs planned trips.
4. WHEN an ad hoc trip is raised THEN the system SHALL apply a configurable response lag between detection and attendance, and SHALL NOT assume instantaneous dispatch.
5. WHEN an ad hoc trip loads cash THEN the system SHALL reset that ATM's 15-day refill clock.
6. IF an ad hoc trip attends a fault without loading cash THEN the system SHALL NOT reset the 15-day refill clock.
7. WHEN an ATM has an ad hoc trip on a given day THEN the system SHALL NOT also schedule a planned refill for that ATM on that day, honouring the once-per-day rule.
8. WHEN a fault attendance coincides with the ATM being within its refill band THEN the system SHALL prefer to combine the fault attendance and the cash refill into a single visit.
9. WHEN an ad hoc trip occurs THEN the system SHALL record its trigger, being low cash or fault, so that root causes can be reported separately.
10. WHEN the schedule for subsequent days is regenerated THEN the system SHALL incorporate completed ad hoc trips as actual refills, so that the plan reflects the true fleet state.

### Requirement 10: Replenishment quantity and denomination mix

**User Story:** As a treasury analyst, I want control over how much cash is loaded and in which denominations, so that we are not funding idle cash in low-traffic machines.

#### Acceptance Criteria

1. WHEN an ATM is scheduled for a refill THEN the system SHALL specify the load amount per cartridge, not only a total figure.
2. WHEN determining a load amount THEN the system SHALL account for the cash remaining in the ATM at the projected time of the visit.
3. WHEN determining a load amount THEN the system SHALL ensure the loaded quantity covers forecast demand at the configured safety quantile through to the next planned visit.
4. WHEN a cartridge load is specified THEN the system SHALL NOT exceed the 2,000-note physical cartridge capacity.
5. WHEN an ATM's demand profile is materially mismatched to its current denomination configuration THEN the system SHALL be able to recommend a revised cartridge denomination mix, so that effective capacity is reshaped to extend the refill interval without additional trips.
6. WHEN a denomination mix change is recommended THEN the system SHALL report the projected change in refill frequency attributable to that change.
7. WHEN denomination mix recommendations are produced THEN the system SHALL emit them as a separate configuration change report rather than as part of the daily trip plan, since re-mixing is a field engineering task rather than a scheduling decision.

### Requirement 11: Independent schedule validation

**User Story:** As a reviewer, I want constraint compliance checked by a component separate from the optimiser, so that compliance is proven rather than assumed.

#### Acceptance Criteria

1. WHEN any schedule is submitted for validation THEN the system SHALL verify it using logic independent of the optimiser that produced it.
2. WHEN a schedule is validated THEN the system SHALL check the daily trip cap inclusive of ad hoc trips, the once-per-ATM-per-day rule, the 15-day maximum refill gap, the daily priority mix apportionment, and adherence to each ATM's refill window.
3. WHEN a schedule is validated THEN the system SHALL confirm that no planned refill targets an ATM projected above 55% of capacity except where recorded as compliance-driven.
4. WHEN a validation check fails THEN the system SHALL report the specific constraint breached, the date, and the ATMs involved.
5. WHEN a schedule passes all checks THEN the system SHALL emit an explicit confirmation covering every constraint evaluated.
6. WHEN validation is run THEN the system SHALL evaluate every day in the schedule horizon rather than sampling.

### Requirement 12: Simulation and baseline benchmarking

**User Story:** As a stakeholder, I want the planner measured against the policies it is meant to replace, so that the improvement is quantified rather than asserted.

#### Acceptance Criteria

1. WHEN a simulation is run THEN the system SHALL step hourly, so that threshold crossings, fault detections, and refill timings are resolved at telemetry granularity.
2. WHEN a simulation is run THEN the system SHALL replay generated demand against a schedule and SHALL track each ATM's per-cartridge balance over time.
3. WHEN replaying a schedule THEN the system SHALL apply each refill at the hour implied by its assigned priority window, so that intraday timing affects the outcome.
4. WHEN an ATM crosses the 25% threshold during simulation THEN the system SHALL raise and dispatch an ad hoc trip according to Requirement 9, including the response lag.
5. WHEN a simulation completes THEN the system SHALL report the number of stockout events, defined as an ATM reaching zero available cash in any denomination it is configured to dispense.
6. WHEN a simulation completes THEN the system SHALL separately report low-cash breach hours, defined as hours an ATM sat below 25% of capacity, and SHALL NOT conflate these with stockouts.
7. WHEN a simulation completes THEN the system SHALL report unmet demand arising from either cash unavailability or disabling faults.
8. WHEN benchmarking THEN the system SHALL evaluate the planner against a reactive threshold-triggered baseline policy.
9. WHEN benchmarking THEN the system SHALL evaluate the planner against a fixed-interval calendar baseline policy.
10. WHEN benchmarking THEN the system SHALL run all policies against identical demand and fault realisations, so that the comparison is not confounded by differing random draws.
11. WHEN benchmarking completes THEN the system SHALL report results for all policies in a single comparison table.

### Requirement 13: Performance reporting

**User Story:** As a stakeholder, I want the challenge success criteria reported directly, so that I can see whether the stated goals were met.

#### Acceptance Criteria

1. WHEN performance is reported THEN the system SHALL report total trips, mean trips per day, and peak trips per day, with planned and ad hoc trips broken out separately.
2. WHEN performance is reported THEN the system SHALL report the ad hoc trip rate as a proportion of all trips, and SHALL report its reduction relative to each baseline policy.
3. WHEN performance is reported THEN the system SHALL report ad hoc trips split by trigger, being low cash or fault, so that avoidable and unavoidable unplanned work are distinguished.
4. WHEN performance is reported THEN the system SHALL report stockout count, targeting zero.
5. WHEN performance is reported THEN the system SHALL report the reduction in total trip count relative to each baseline policy, expressed as both an absolute and a percentage figure.
6. WHEN performance is reported THEN the system SHALL report idle cash held in the field over time, as a measure of working capital efficiency.
7. WHEN performance is reported THEN the system SHALL report the distribution of ATM fill percentage at the moment of refill, and SHALL show what proportion of refills occurred inside the 25% to 55% band, as the direct measure of just-in-time behaviour.
8. WHEN performance is reported THEN the system SHALL report the count of compliance-driven low-value trips, being visits forced by the 15-day rule to ATMs above 55% of capacity, so that the cost of that policy is quantified.
9. WHEN performance is reported THEN the system SHALL report the distribution of intervals between consecutive refills, and SHALL confirm that no interval exceeded 15 days.
10. WHEN performance is reported THEN the system SHALL report the count of hard constraint violations, targeting zero.

### Requirement 14: Operator dashboard

**User Story:** As an operations manager, I want to see and interrogate the day's plan, so that I can act on it and understand the fleet's risk position.

#### Acceptance Criteria

1. WHEN the dashboard is opened THEN the system SHALL display the current day's plan grouped by P0, P1, and P2 window.
2. WHEN a day's plan is displayed THEN the system SHALL show each scheduled ATM's identifier, location, current balance, balance as a percentage of capacity, refill window, load amount, and the reason it was scheduled.
3. WHEN the dashboard is opened THEN the system SHALL display any open ad hoc trips, their trigger, and their age.
4. WHEN the dashboard is opened THEN the system SHALL display a fleet-wide risk view identifying ATMs inside their refill band and those approaching the 25% threshold.
5. WHEN the dashboard is opened THEN the system SHALL display the performance metrics defined in Requirement 13.
6. WHEN a user adjusts a planning parameter THEN the system SHALL allow the plan to be regenerated and SHALL show the effect on trip count, ad hoc rate, and idle cash.
7. WHEN a user selects an individual ATM THEN the system SHALL display its hourly balance history, its demand forecast with uncertainty band, its refill window, and its projected balance trajectory against the 55% and 25% levels.

### Requirement 15: Decision explainability

**User Story:** As an operations manager, I want plain-language reasons for scheduling decisions, so that I can trust and defend the plan.

#### Acceptance Criteria

1. WHEN an ATM is scheduled THEN the system SHALL state in plain language why it was scheduled on that date and in that priority window.
2. WHEN an ATM is inside its refill band but not scheduled THEN the system SHALL state why deferral is considered safe, referencing its projected 25% breach date.
3. WHEN an explanation is generated THEN the system SHALL cite the underlying figures, including forecast demand, refill window boundaries, and the binding constraint.
4. WHEN a visit is compliance-driven THEN the system SHALL say so explicitly rather than implying a cash need.
5. WHEN a user queries the plan in natural language THEN the system SHALL answer using the actual schedule, telemetry, and forecast data rather than generating an unsupported response.

### Requirement 16: Configuration and reproducibility

**User Story:** As a developer, I want the system's parameters externalised and its runs reproducible, so that results can be verified and tuned without code changes.

#### Acceptance Criteria

1. WHEN the system is configured THEN the system SHALL externalise the daily trip cap, the priority mix percentages, the maximum refill gap, the refill eligibility percentage, the low-cash threshold percentage, the safety quantile, the planning horizon, the ad hoc capacity reserve, the ad hoc response lag, the ad hoc cost weighting, and the objective weights.
2. WHEN a business rule is changed via configuration THEN the system SHALL apply it without requiring code modification.
3. WHEN the refill eligibility and low-cash threshold percentages are configured THEN the system SHALL verify that eligibility is strictly greater than the threshold and SHALL reject configurations where it is not.
4. WHEN any stochastic component runs THEN the system SHALL accept an explicit random seed.
5. WHEN a pipeline run completes THEN the system SHALL record the configuration and seed used, so that the run can be reproduced.
6. WHEN a configuration value violates a documented bound THEN the system SHALL reject it with a message identifying the offending parameter.

---

## Out of scope

- Vehicle routing, travel time, crew rostering, and depot assignment, following the one-trip-equals-one-visit decision.
- Integration with live ATM telemetry or core banking systems; the hourly feed is synthesised.
- Cash-in-transit security, custody, and reconciliation processes.
- Technician skill matching and spare parts logistics for fault resolution; faults are modelled only as trip demand and outage.
- Deposit-taking or recycling ATMs; the model assumes dispense-only machines.
- Multi-currency handling.
