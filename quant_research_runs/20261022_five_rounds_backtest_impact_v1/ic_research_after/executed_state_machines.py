def run_real_maturity(admission: pd.Series, threshold: float | None, maturity: str = 'm1', option_one_way: float = ONE_WAY):
    active, etf, chains, options, expiries, futures = real_inputs()
    equity, state, pos, pending_exit, pending_roll, cycle = 1.0, "idle", None, False, False, None
    rows, events, cycles, skips = [], [], [], 0
    label = f"real_{maturity}_hold" if threshold is None else f"real_{maturity}_decay_{int(threshold * 100)}"
    for i, b in active.iterrows():
        day = pd.Timestamp(b.date); previous = equity; pnl = cost = 0.0; action = ""
        etf_open, etf_close = float(etf.loc[day, "open"]), float(etf.loc[day, "close"])
        if state == "short_put":
            q = options.loc[(pos["security_id"], day)]
            if not (q.close > 0 and q.volume > 0): raise RuntimeError(f"Unmarkable option {day.date()}")
            pnl += pos["contracts"] * real_source.ETF_MULTIPLIER * (pos["mark"] - float(q.close)); pos["mark"] = float(q.close)
            if pending_roll:
                prior_spot = float(etf.loc[pd.Timestamp(active.loc[i - 1, "date"]), "close"])
                next_month = pd.Timestamp(pos["contract_month"]) + pd.offsets.MonthBegin(1)
                selected = choose_real(chains.get(day, pd.DataFrame()), prior_spot, next_month) if bool(admission.get(day, False)) else None
                if selected is not None:
                    nq = options.loc[(selected.security_id, day)]
                    if nq.open > 0 and nq.close > 0 and nq.volume > 0:
                        # Marked old/new legs at close, then execute both sides at open.
                        pnl += pos["contracts"] * real_source.ETF_MULTIPLIER * (float(q.close) - float(q.open))
                        pnl += pos["contracts"] * real_source.ETF_MULTIPLIER * (float(nq.open) - float(nq.close))
                        cost += pos["contracts"] * real_source.ETF_MULTIPLIER * prior_spot * (2 * option_one_way)
                        pos.update(security_id=str(selected.security_id), contract=str(selected.contract_id), strike=float(selected.strike), mark=float(nq.close), expiry=pd.Timestamp(expiries.loc[selected.security_id]), contract_month=pd.Timestamp(selected.contract_month), entry_premium=float(nq.open), rolled=True, roll_wait_reason="")
                        cycle["early_rolls"] += 1; cycle["last_roll_date"] = str(day.date()); action = "early_roll_buyback_and_sell_next_open"
                        pending_roll = False
                    else:
                        reason = "early_roll_untradable_new_leg"
                        if pos.get("roll_wait_reason") != reason: action = reason
                        pos["roll_wait_reason"] = reason
                else:
                    reason = "early_roll_blocked_re_admission"
                    if pos.get("roll_wait_reason") != reason: action = reason
                    pos["roll_wait_reason"] = reason
            if day == pos["expiry"]:
                intrinsic = max(pos["strike"] - etf_close, 0.0); pnl += pos["contracts"] * real_source.ETF_MULTIPLIER * (pos["mark"] - intrinsic)
                if intrinsic > 0:
                    pos = {"shares": pos["contracts"] * real_source.ETF_MULTIPLIER, "etf_mark": etf_close}; state = "pending_etf_to_ic"; action = "physical_assignment_pending_etf_to_ic"; cycle["physical_assignment_date"] = str(day.date())
                else:
                    cycle["realized_pnl"] += pnl - cost; cycle.update(exit_date=str(day.date()), exit_reason="worthless_expiry", closed=True); cycles.append(cycle.copy()); cycle = pos = None; state = "idle"; action = "worthless_expiry"
        elif state == "pending_etf_to_ic":
            shares = pos["shares"]; pnl += shares * (etf_open - pos["etf_mark"]); cost += shares * etf_open * ONE_WAY
            fq = futures.loc[(b.contract, day)]; units = shares * etf_open / (float(fq.open) * real_source.IC_MULTIPLIER); cost += units * real_source.IC_MULTIPLIER * float(fq.open) * ONE_WAY; pnl += units * real_source.IC_MULTIPLIER * (float(fq.settle) - float(fq.open))
            pos = {"contract": str(b.contract), "units": units, "mark": float(fq.settle)}; state = "ic_future"; action = "assignment_sell_etf_buy_ic_next_open"; cycle["ic_conversion_date"] = str(day.date())
        elif state == "ic_future":
            fq = futures.loc[(pos["contract"], day)]
            if pending_exit:
                pnl += pos["units"] * real_source.IC_MULTIPLIER * (float(fq.open) - pos["mark"]); cost += pos["units"] * real_source.IC_MULTIPLIER * float(fq.open) * ONE_WAY; cycle["realized_pnl"] += pnl - cost; cycle.update(exit_date=str(day.date()), exit_reason="recovery_exit_ic_next_open", closed=True, recovery_days=int((day - pd.Timestamp(cycle["ic_conversion_date"])).days)); cycles.append(cycle.copy()); cycle = pos = None; state = "idle"; pending_exit = False; action = "recovery_exit_ic_next_open"
            elif pd.notna(b.roll_to) and str(b.roll_to) != pos["contract"]:
                nq = futures.loc[(str(b.roll_to), day)]; pnl += pos["units"] * real_source.IC_MULTIPLIER * (float(fq.close) - pos["mark"]); cost += pos["units"] * real_source.IC_MULTIPLIER * (float(fq.close) + float(nq.close)) * ONE_WAY; pnl += pos["units"] * real_source.IC_MULTIPLIER * (float(nq.settle) - float(nq.close)); pos.update(contract=str(b.roll_to), mark=float(nq.settle)); action = "ic_monthly_roll_close"
            else: pnl += pos["units"] * real_source.IC_MULTIPLIER * (float(fq.settle) - pos["mark"]); pos["mark"] = float(fq.settle)
        if state == "idle" and i > 0 and not action and bool(admission.get(day, False)):
            prior_spot = float(etf.loc[pd.Timestamp(active.loc[i - 1, "date"]), "close"]); chain_today = chains.get(day, pd.DataFrame()); month = real_entry_month(day, chain_today, expiries, pd.DatetimeIndex(active.date), maturity); selected = choose_real(chain_today, prior_spot, month)
            if selected is None: skips += 1; action = "skip_missing_next_month_chain"
            else:
                q = options.loc[(selected.security_id, day)]
                if q.open > 0 and q.volume > 0:
                    contracts = previous / (prior_spot * real_source.ETF_MULTIPLIER); pos = {"security_id": str(selected.security_id), "contract": str(selected.contract_id), "strike": float(selected.strike), "contracts": contracts, "mark": float(q.open), "expiry": pd.Timestamp(expiries.loc[selected.security_id]), "contract_month": pd.Timestamp(selected.contract_month), "entry_premium": float(q.open), "rolled": False, "roll_wait_reason": ""}; cycle = {"candidate": label, "entry_date": str(day.date()), "put_contract": str(selected.contract_id), "entry_moneyness": float(selected.strike) / prior_spot, "contracts": contracts, "entry_contract_month": str(month.date()), "maturity": maturity, "closed": False, "realized_pnl": 0.0, "early_rolls": 0, "last_roll_date": ""}; cost += contracts * real_source.ETF_MULTIPLIER * prior_spot * option_one_way; state = "short_put"; action = "sell_next_month_95_put_open"
                else: skips += 1; action = "skip_untradable_nearest_strike"
        if cycle is not None: cycle["realized_pnl"] += pnl - cost
        cash_weight = 1.0 if state == "idle" else 0.70; cash = previous * cash_weight * real_source.CASH_DAILY; equity += pnl - cost + cash
        if not np.isfinite(equity) or equity <= 0: raise RuntimeError(f"Invalid NAV {day}")
        if state == "short_put" and threshold is not None and not pos["rolled"] and not pending_roll and not action and pos["mark"] <= pos["entry_premium"] * (1 - threshold): pending_roll = True; pos["roll_wait_reason"] = ""; action = f"premium_decay_{int(threshold*100)}_signal_close"
        if state == "ic_future" and cycle["realized_pnl"] >= pos["units"] * real_source.IC_MULTIPLIER * pos["mark"] * ONE_WAY: pending_exit = True
        row = {"date": day, "candidate": label, "return_net": equity / previous - 1, "nav": equity, "pnl": pnl, "cost": cost, "cash": cash, "cash_weight": cash_weight, "state": state, "action": action, "pending_roll": pending_roll}
        rows.append(row)
        if action: events.append(row.copy())
    if cycle is not None: cycle.update(mark_date=str(active.date.iloc[-1].date()), open_cycle_pnl=cycle["realized_pnl"]); cycles.append(cycle.copy())
    daily, cycles = pd.DataFrame(rows), pd.DataFrame(cycles)
    if "physical_assignment_date" not in cycles: cycles["physical_assignment_date"] = pd.NaT
    if "early_rolls" not in cycles: cycles["early_rolls"] = 0
    if "open_cycle_pnl" not in cycles: cycles["open_cycle_pnl"] = np.nan
    closed = cycles.closed.fillna(False); ledger = abs((daily.pnl - daily.cost).sum() - cycles.loc[closed, "realized_pnl"].sum() - cycles.loc[~closed, "open_cycle_pnl"].fillna(0).sum())
    if ledger > 1e-11: raise RuntimeError(f"Real ledger {ledger}")
    return daily, pd.DataFrame(events), cycles, {"rows": len(daily), "cycles": len(cycles), "assignments": int(cycles.physical_assignment_date.notna().sum()), "early_rolls": int(cycles.early_rolls.sum()), "ledger_max_abs_error": float(ledger), "entry_skips": skips}


def run_model_maturity(admission: pd.Series, threshold: float | None, maturity: str = 'm1', option_one_way: float = ONE_WAY):
    active, market, futures = model_source.model_inputs()
    market = market.set_index("date").loc[active.date].reset_index()
    dates = pd.DatetimeIndex(active.date); equity, state, pos, pending_exit, pending_roll, cycle = 1.0, "idle", None, False, False, None
    rows, events, cycles = [], [], []
    label = f"model_{maturity}_hold" if threshold is None else f"model_{maturity}_decay_{int(threshold * 100)}"
    def expiry(month): return proxy.fourth_wednesday(month, dates)
    def price(row, strike, ex, field):
        years = max((ex - pd.Timestamp(row.date)).days / 365.0, 0.0)
        return proxy.bs_put(float(getattr(row, f"spot_{field}")), strike, float(getattr(row, f"rate_{field}")), float(getattr(row, f"dividend_{field}")), float(getattr(row, f"sigma_{field}")), years)
    for i, b in active.iterrows():
        day = pd.Timestamp(b.date); m = market.iloc[i]; previous = equity; pnl = cost = 0.0; action = ""
        if state == "short_put":
            mark = price(m, pos["strike"], pos["expiry"], "close"); pnl += pos["units"] * (pos["mark"] - mark); pos["mark"] = mark
            if pending_roll:
                if bool(admission.get(day, False)):
                    month = pos["contract_month"] + pd.offsets.MonthBegin(1); ex = expiry(month); strike = float(market.iloc[i-1].spot_close) * .95; old_open = price(m, pos["strike"], pos["expiry"], "open"); new_open = price(m, strike, ex, "open"); new_close = price(m, strike, ex, "close")
                    pnl += pos["units"] * (mark - old_open) + pos["units"] * (new_open - new_close); cost += pos["units"] * float(market.iloc[i-1].spot_close) * 2 * option_one_way; pos.update(strike=strike, expiry=ex, contract_month=month, mark=new_close, entry_premium=new_open, rolled=True, roll_wait_reason=""); cycle["early_rolls"] += 1; cycle["last_roll_date"] = str(day.date()); action = "early_roll_buyback_and_sell_next_open"; pending_roll = False
                else:
                    reason = "early_roll_blocked_re_admission"
                    if pos.get("roll_wait_reason") != reason: action = reason
                    pos["roll_wait_reason"] = reason
            if day == pos["expiry"]:
                intrinsic = max(pos["strike"] - float(m.spot_close), 0.0); pnl += pos["units"] * (pos["mark"] - intrinsic)
                if intrinsic > 0: pos = {"shares": pos["units"], "mark": float(m.spot_close)}; state = "assigned_etf"; cycle["physical_assignment_date"] = str(day.date()); action = "physical_assignment_proxy"
                else: cycle["realized_pnl"] += pnl-cost; cycle.update(exit_date=str(day.date()), exit_reason="worthless_expiry", closed=True); cycles.append(cycle.copy()); cycle = pos = None; state = "idle"; action = "worthless_expiry"
        elif state == "assigned_etf":
            shares = pos["shares"]; pnl += shares * (float(m.spot_open) - pos["mark"]); cost += shares * float(m.spot_open) * ONE_WAY; fq = futures.loc[(str(b.contract), day)]; units = shares * float(m.spot_open) / (float(fq.open) * real_source.IC_MULTIPLIER); cost += units * real_source.IC_MULTIPLIER * float(fq.open) * ONE_WAY; pnl += units * real_source.IC_MULTIPLIER * (float(fq.settle)-float(fq.open)); pos={"contract":str(b.contract),"units":units,"mark":float(fq.settle)};state="ic_future";cycle["ic_conversion_date"]=str(day.date());action="assignment_sell_proxy_etf_buy_ic_next_open"
        elif state == "ic_future":
            fq=futures.loc[(pos["contract"],day)]
            if pending_exit:
                pnl += pos["units"]*real_source.IC_MULTIPLIER*(float(fq.open)-pos["mark"]);cost += pos["units"]*real_source.IC_MULTIPLIER*float(fq.open)*ONE_WAY;cycle["realized_pnl"] += pnl-cost;cycle.update(exit_date=str(day.date()),exit_reason="recovery_exit_ic_next_open",closed=True,recovery_days=int((day-pd.Timestamp(cycle["ic_conversion_date"])).days));cycles.append(cycle.copy());cycle=pos=None;state="idle";pending_exit=False;action="recovery_exit_ic_next_open"
            elif pd.notna(b.roll_to) and str(b.roll_to)!=pos["contract"]:
                nq=futures.loc[(str(b.roll_to),day)];pnl += pos["units"]*real_source.IC_MULTIPLIER*(float(fq.close)-pos["mark"]);cost += pos["units"]*real_source.IC_MULTIPLIER*(float(fq.close)+float(nq.close))*ONE_WAY;pnl += pos["units"]*real_source.IC_MULTIPLIER*(float(nq.settle)-float(nq.close));pos.update(contract=str(b.roll_to),mark=float(nq.settle));action="ic_monthly_roll_close"
            else: pnl += pos["units"]*real_source.IC_MULTIPLIER*(float(fq.settle)-pos["mark"]);pos["mark"]=float(fq.settle)
        if state == "idle" and i > 0 and not action and bool(admission.get(day,False)):
            month=model_entry_month(day,dates,expiry,maturity);ex=expiry(month);strike=float(market.iloc[i-1].spot_close)*.95;op=price(m,strike,ex,"open");pos={"units":previous/float(market.iloc[i-1].spot_close),"strike":strike,"expiry":ex,"contract_month":month,"mark":op,"entry_premium":op,"rolled":False,"roll_wait_reason":""};cycle={"candidate":label,"entry_date":str(day.date()),"entry_contract_month":str(month.date()),"maturity":maturity,"closed":False,"realized_pnl":0.0,"early_rolls":0,"last_roll_date":""};cost += pos["units"]*float(market.iloc[i-1].spot_close)*option_one_way;state="short_put";action="sell_model_next_month_95_put_open"
        if cycle is not None: cycle["realized_pnl"] += pnl-cost
        cash_weight=1.0 if state=="idle" else .70;cash=previous*cash_weight*model_source.CASH;equity += pnl-cost+cash
        if not np.isfinite(equity) or equity<=0: raise RuntimeError(f"Invalid model NAV {day}")
        if state=="short_put" and threshold is not None and not pos["rolled"] and not pending_roll and not action and pos["mark"] <= pos["entry_premium"]*(1-threshold):pending_roll=True;pos["roll_wait_reason"]="";action=f"premium_decay_{int(threshold*100)}_signal_close"
        if state=="ic_future" and cycle["realized_pnl"] >= pos["units"]*real_source.IC_MULTIPLIER*pos["mark"]*ONE_WAY:pending_exit=True
        row={"date":day,"candidate":label,"return_net":equity/previous-1,"nav":equity,"pnl":pnl,"cost":cost,"cash":cash,"cash_weight":cash_weight,"state":state,"action":action,"pending_roll":pending_roll};rows.append(row)
        if action:events.append(row.copy())
    if cycle is not None: cycle.update(mark_date=str(dates[-1].date()),open_cycle_pnl=cycle["realized_pnl"]);cycles.append(cycle.copy())
    daily,cycles=pd.DataFrame(rows),pd.DataFrame(cycles)
    if "physical_assignment_date" not in cycles:cycles["physical_assignment_date"]=pd.NaT
    if "early_rolls" not in cycles:cycles["early_rolls"]=0
    if "open_cycle_pnl" not in cycles:cycles["open_cycle_pnl"]=np.nan
    closed=cycles.closed.fillna(False);ledger=abs((daily.pnl-daily.cost).sum()-cycles.loc[closed,"realized_pnl"].sum()-cycles.loc[~closed,"open_cycle_pnl"].fillna(0).sum())
    if ledger>1e-11:raise RuntimeError(f"Model ledger {ledger}")
    return daily,pd.DataFrame(events),cycles,{"rows":len(daily),"cycles":len(cycles),"assignments":int(cycles.physical_assignment_date.notna().sum()),"early_rolls":int(cycles.early_rolls.sum()),"ledger_max_abs_error":float(ledger)}


def run_model_delta(
    ic: pd.DataFrame,
    schedule: pd.DataFrame,
    market: pd.DataFrame,
    label: str,
    roll_dates: set[pd.Timestamp], open_exit_dates=frozenset(), profit_multiple=None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    proxy = v19.v18.v13.proxy
    v6 = v19.v18.v13.v6
    daily = ic[ic["date"] >= MODEL_START].copy().reset_index(drop=True)
    daily["prior_settle"] = daily["settle"].shift(1)
    daily.loc[0, "prior_settle"] = daily.loc[0, "settle"]
    merged = daily.merge(market.drop(columns=["settle"]), on="date", validate="one_to_one")
    events = _schedule_events(schedule, "model")
    trade_dates = pd.DatetimeIndex(ic["date"])
    active: DeltaModelPosition | None = None
    latest_target = 0.0
    latest_tier = 0
    latest_eval: pd.Timestamp | None = None
    rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    entry_premium = np.nan
    pending_profit_since = None

    def open_position(row: object, target: float) -> tuple[DeltaModelPosition, float, float]:
        day = pd.Timestamp(row.date)
        month = v6.desired_model_month(day, TENOR, trade_dates)
        expiry = proxy.fourth_wednesday(month, trade_dates)
        strike = float(row.spot_close) * MONEYNESS
        shell = DeltaModelPosition(month, expiry, strike, 0.0, 0.0, target, 0.0)
        price, delta = model_price_and_delta(shell, row)
        absolute_delta = abs(delta)
        if absolute_delta <= 1e-8:
            raise RuntimeError("Model entry Delta too small for sizing")
        notional_fraction = target / absolute_delta
        units = (
            float(row.settle)
            * IC_MULTIPLIER
            / float(row.spot_close)
            * notional_fraction
        )
        position = DeltaModelPosition(
            month, expiry, strike, units, notional_fraction, target, price
        )
        return position, absolute_delta, abs(notional_fraction * absolute_delta - target)

    for row in merged.itertuples(index=False):
        day = pd.Timestamp(row.date)
        route_open_exit = day in open_exit_dates
        event = events.get(day)
        changed = False
        if event is not None:
            new_target = float(event.target_delta)
            changed = not math.isclose(new_target, latest_target, abs_tol=1e-12)
            latest_target = new_target
            latest_tier = int(event.risk_tier)
            latest_eval = pd.Timestamp(event.eval_date)
        denominator = float(row.prior_settle) * IC_MULTIPLIER
        pnl = 0.0
        cost = 0.0
        action = ""
        old = active
        old_notional = active.notional_fraction if active is not None else 0.0
        entry_delta = np.nan
        target_error = np.nan
        monthly_request = bool(day in roll_dates and active is not None and latest_target > 0)
        profit_request = bool(active is not None and pending_profit_since is not None and not monthly_request and not changed and not route_open_exit)

        if active is None and latest_target > 0:
            active, entry_delta, target_error = open_position(row, latest_target)
            cost += active.notional_fraction * PUT_SIDE_COST
            action = "close_buy"
            entry_premium = float(active.prior_mark)
            pending_profit_since = None
        elif active is not None and latest_target == 0:
            if route_open_exit:
                years=max((active.expiry-day).days,0)/365.0
                price=v19.v18.v13.proxy.bs_put(float(row.spot_open),active.strike,float(row.rate_open),float(row.dividend_open),float(row.sigma_open),years)
            else:
                price, _ = model_price_and_delta(active, row)
            pnl += active.units * (price - active.prior_mark) / denominator
            cost += active.notional_fraction * PUT_SIDE_COST
            active = None
            entry_premium = np.nan
            pending_profit_since = None
            action = "route_open_exit" if route_open_exit else "close_exit"
        elif active is not None and monthly_request:
            old_price, _ = model_price_and_delta(active, row)
            pnl += active.units * (old_price - active.prior_mark) / denominator
            new_position, entry_delta, target_error = open_position(row, latest_target)
            cost += (active.notional_fraction + new_position.notional_fraction) * PUT_SIDE_COST
            active = new_position
            entry_premium = float(active.prior_mark)
            pending_profit_since = None
            action = "close_roll_monthly"
        elif active is not None and profit_request:
            old_price, _ = model_price_and_delta(active, row)
            pnl += active.units * (old_price - active.prior_mark) / denominator
            new_position, entry_delta, target_error = open_position(row, latest_target)
            cost += (active.notional_fraction + new_position.notional_fraction) * PUT_SIDE_COST
            active = new_position
            entry_premium = float(active.prior_mark)
            pending_profit_since = None
            action = "close_profit_restrike"
        elif active is not None and changed:
            price, delta = model_price_and_delta(active, row)
            pnl += active.units * (price - active.prior_mark) / denominator
            entry_delta = abs(delta)
            if entry_delta <= 1e-8:
                raise RuntimeError("Model resize Delta too small for sizing")
            new_notional = latest_target / entry_delta
            new_units = (
                float(row.settle)
                * IC_MULTIPLIER
                / float(row.spot_close)
                * new_notional
            )
            cost += abs(new_notional - active.notional_fraction) * PUT_SIDE_COST
            active.units = new_units
            active.notional_fraction = new_notional
            active.target_delta = latest_target
            active.prior_mark = price
            target_error = abs(new_notional * entry_delta - latest_target)
            action = "close_resize"
        elif active is not None:
            price, _ = model_price_and_delta(active, row)
            pnl += active.units * (price - active.prior_mark) / denominator
            active.prior_mark = price

        if active is not None and profit_multiple is not None and action != "close_profit_restrike" and np.isfinite(entry_premium) and active.prior_mark >= entry_premium * float(profit_multiple):
            pending_profit_since = pending_profit_since or day

        if action:
            trades.append(
                {
                    "candidate": label,
                    "signal_eval_date": latest_eval,
                    "scheduled_execution_date": day,
                    "actual_execution_date": day,
                    "action": action,
                    "risk_tier": latest_tier,
                    "target_delta": latest_target,
                    "target_delta_error": target_error,
                    "entry_abs_delta": entry_delta,
                    "delta_source": "model_qvix",
                    "old_notional_fraction": old_notional,
                    "new_notional_fraction": active.notional_fraction if active else 0.0,
                    "old_contract": "",
                    "new_contract": "",
                    "old_month": old.contract_month if old else pd.NaT,
                    "new_month": active.contract_month if active else pd.NaT,
                    "new_strike": active.strike if active else np.nan,
                    "new_entry_moneyness": MONEYNESS if active else np.nan,
                    "delay_days": 0,
                    "delay_trading_days": 0,
                    "forced_month_roll": action == "close_roll_monthly",
                    "roll_request_date": day if action == "close_roll_monthly" else pd.NaT,
                    "same_month_reset": bool(
                        old is not None
                        and active is not None
                        and old.contract_month == active.contract_month
                    ),
                }
            )

        expired = False
        if active is not None and active.expiry == day:
            expired = True
            active = None
            entry_premium = np.nan
            pending_profit_since = None
        mark_fraction = 0.0
        contract = ""
        units = 0.0
        notional_fraction = 0.0
        effective_delta = 0.0
        absolute_delta = np.nan
        if active is not None:
            _, current_delta = model_price_and_delta(active, row)
            absolute_delta = abs(current_delta)
            notional_fraction = (
                active.units
                * float(row.spot_close)
                / (float(row.settle) * IC_MULTIPLIER)
            )
            effective_delta = notional_fraction * absolute_delta
            mark_fraction = (
                active.units * active.prior_mark / (float(row.settle) * IC_MULTIPLIER)
            )
            contract = f"MODEL_{active.contract_month.strftime('%y%m')}_{active.strike:.6f}"
            units = active.units
        rows.append(
            {
                "date": day,
                "candidate": label,
                "put_pnl_ret": pnl,
                "put_cost_rate": cost,
                "put_mark_fraction": mark_fraction,
                "put_contract": contract,
                "put_qty": units,
                "target_fraction": latest_target,
                "signal_target_fraction": latest_target,
                "target_delta": latest_target,
                "risk_tier": latest_tier,
                "actual_notional_fraction": notional_fraction,
                "abs_put_delta": absolute_delta,
                "effective_delta_hedge_ratio": effective_delta,
                "delta_source": "model_qvix" if active is not None else "",
                "entry_moneyness_mark": MONEYNESS if active is not None else np.nan,
                "carried_mark": False,
                "mark_stale_days": 0,
                "deferred_adjustment": False,
                "expired": expired,
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(trades)



def run_real_delta(
    ic: pd.DataFrame,
    schedule: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
    market: pd.DataFrame,
    label: str,
    roll_dates: set[pd.Timestamp], open_exit_dates=frozenset(), profit_multiple=None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    proxy = v19.v18.v13.proxy
    v6 = v19.v18.v13.v6
    v11 = v19.v18.v11
    daily = ic[ic["date"] >= REAL_START].copy().reset_index(drop=True)
    offset = len(ic) - len(daily)
    daily["prior_settle"] = ic["settle"].shift(1).loc[daily.index + offset].to_numpy()
    daily.loc[0, "prior_settle"] = float(
        ic.loc[ic["date"] < REAL_START, "settle"].iloc[-1]
    )
    etf = frames["etf500"].set_index("date")
    market_lookup = market.set_index("date")
    histories = frames["histories"]
    history_lookup = histories.set_index(["security_id", "date"])
    history_groups = {
        key: group.sort_values("date") for key, group in histories.groupby("security_id")
    }
    snapshots = frames["snapshots"]
    events = _schedule_events(schedule, "real")
    trade_dates = pd.DatetimeIndex(ic["date"])
    active: DeltaRealPosition | None = None
    latest_target = 0.0
    latest_tier = 0
    latest_eval: pd.Timestamp | None = None
    pending_action_since: pd.Timestamp | None = None
    pending_roll_since: pd.Timestamp | None = None
    rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    entry_premium = np.nan
    pending_profit_since = None

    def select_contract(day: pd.Timestamp, row: object) -> tuple[pd.Series, pd.Series] | None:
        month = v6.desired_real_month(snapshots, day, TENOR, trade_dates)
        if month is None:
            return None
        return v11.select_real_contract_target(
            snapshots,
            history_lookup,
            day,
            month,
            float(etf.loc[day, "close"]),
            MONEYNESS,
        )

    def sized_position(
        day: pd.Timestamp,
        row: object,
        master: pd.Series,
        quote: pd.Series,
        target: float,
    ) -> tuple[DeltaRealPosition, float, float, str]:
        market_row = market_lookup.loc[day]
        etf_close = float(etf.loc[day, "close"])
        month = pd.Timestamp(master["contract_month"])
        expiry = proxy.fourth_wednesday(month, trade_dates)
        delta, sigma, source = real_delta_from_mark(
            float(quote["close"]),
            etf_close,
            float(master["strike"]),
            expiry,
            day,
            float(market_row["rate_close"]),
            float(market_row["dividend_close"]),
            float(market_row["sigma_close"]),
        )
        absolute_delta = abs(delta)
        full_qty = _full_real_qty(row, etf_close)
        qty = _delta_real_qty(full_qty, target, absolute_delta)
        notional = qty / full_qty
        error = abs(notional * absolute_delta - target)
        return (
            DeltaRealPosition(
                str(master["security_id"]),
                str(master["contract_id"]),
                month,
                expiry,
                float(master["strike"]),
                qty,
                full_qty,
                notional,
                target,
                float(quote["close"]),
                float(master["strike"]) / etf_close,
                sigma,
            ),
            absolute_delta,
            error,
            source,
        )

    for row in daily.itertuples(index=False):
        day = pd.Timestamp(row.date)
        route_open_exit = day in open_exit_dates
        event = events.get(day)
        if event is not None:
            new_target = float(event.target_delta)
            if not math.isclose(new_target, latest_target, abs_tol=1e-12):
                pending_action_since = pending_action_since or day
            latest_target = new_target
            latest_tier = int(event.risk_tier)
            latest_eval = pd.Timestamp(event.eval_date)
        if day in roll_dates and active is not None and latest_target > 0:
            pending_roll_since = pending_roll_since or day
        if active is None and latest_target > 0:
            pending_action_since = pending_action_since or day

        denominator = float(row.prior_settle) * IC_MULTIPLIER
        etf_close = float(etf.loc[day, "close"])
        pnl = 0.0
        cost = 0.0
        stale_days = 0
        carried = False
        action = ""
        old = active
        old_notional = active.notional_fraction if active is not None else 0.0
        request_date = pending_roll_since or pending_action_since or day
        entry_delta = np.nan
        target_error = np.nan
        delta_source = ""

        if active is not None and latest_target == 0:
            quote = proxy.history_exact(history_lookup, active.security_id, day)
            exit_price = float(quote["open"] if route_open_exit else quote["close"]) if quote is not None else math.nan
            if quote is not None and exit_price > 0 and float(quote["volume"]) > 0:
                pnl += (
                    active.qty
                    * OPTION_MULTIPLIER
                    * (exit_price - active.prior_mark)
                    / denominator
                )
                cost += active.notional_fraction * PUT_SIDE_COST
                active = None
                entry_premium = np.nan
                pending_profit_since = None
                action = "route_open_exit" if route_open_exit else "close_exit"
                pending_action_since = None
                pending_roll_since = None
        elif active is not None and pending_roll_since is not None:
            selected = select_contract(day, row)
            old_quote = proxy.history_exact(history_lookup, active.security_id, day)
            if (
                selected is not None
                and old_quote is not None
                and float(old_quote["close"]) > 0
                and float(old_quote["volume"]) > 0
            ):
                master, new_quote = selected
                new_position, entry_delta, target_error, delta_source = sized_position(
                    day, row, master, new_quote, latest_target
                )
                pnl += (
                    active.qty
                    * OPTION_MULTIPLIER
                    * (float(old_quote["close"]) - active.prior_mark)
                    / denominator
                )
                cost += (
                    active.notional_fraction + new_position.notional_fraction
                ) * PUT_SIDE_COST
                active = new_position
                entry_premium = float(active.prior_mark)
                pending_profit_since = None
                action = "close_roll_monthly"
                pending_roll_since = None
                pending_action_since = None
        elif active is None and latest_target > 0:
            selected = select_contract(day, row)
            if selected is not None:
                master, quote = selected
                active, entry_delta, target_error, delta_source = sized_position(
                    day, row, master, quote, latest_target
                )
                cost += active.notional_fraction * PUT_SIDE_COST
                action = "close_buy"
                entry_premium = float(active.prior_mark)
                pending_profit_since = None
                pending_action_since = None
        elif active is not None and pending_action_since is not None:
            quote = proxy.history_exact(history_lookup, active.security_id, day)
            if quote is not None and float(quote["close"]) > 0 and float(quote["volume"]) > 0:
                market_row = market_lookup.loc[day]
                delta, sigma, delta_source = real_delta_from_mark(
                    float(quote["close"]),
                    etf_close,
                    active.strike,
                    active.expiry,
                    day,
                    float(market_row["rate_close"]),
                    float(market_row["dividend_close"]),
                    float(market_row["sigma_close"]),
                )
                entry_delta = abs(delta)
                full_qty = _full_real_qty(row, etf_close)
                new_qty = _delta_real_qty(full_qty, latest_target, entry_delta)
                new_notional = new_qty / full_qty
                target_error = abs(new_notional * entry_delta - latest_target)
                pnl += (
                    active.qty
                    * OPTION_MULTIPLIER
                    * (float(quote["close"]) - active.prior_mark)
                    / denominator
                )
                cost += abs(new_notional - active.notional_fraction) * PUT_SIDE_COST
                active.qty = new_qty
                active.full_qty = full_qty
                active.notional_fraction = new_notional
                active.target_delta = latest_target
                active.prior_mark = float(quote["close"])
                active.last_iv = sigma
                action = "close_resize"
                pending_action_since = None

        elif active is not None and pending_profit_since is not None:
            profit_request_date = pending_profit_since
            selected = select_contract(day, row)
            old_quote = proxy.history_exact(history_lookup, active.security_id, day)
            if selected is not None and old_quote is not None and float(old_quote["close"]) > 0 and float(old_quote["volume"]) > 0:
                master, new_quote = selected
                new_position, entry_delta, target_error, delta_source = sized_position(day, row, master, new_quote, latest_target)
                pnl += active.qty * OPTION_MULTIPLIER * (float(old_quote["close"]) - active.prior_mark) / denominator
                cost += (active.notional_fraction + new_position.notional_fraction) * PUT_SIDE_COST
                active = new_position
                entry_premium = float(active.prior_mark)
                action = "close_profit_restrike"
                pending_profit_since = None

        if not action and active is not None:
            mark, stale_days, carried = proxy.real_mark(
                history_groups, active, day, etf_close
            )
            pnl += (
                active.qty * OPTION_MULTIPLIER * (mark - active.prior_mark) / denominator
            )
            active.prior_mark = mark

        if active is not None and profit_multiple is not None and action != "close_profit_restrike" and np.isfinite(entry_premium):
            trigger_quote = proxy.history_exact(history_lookup, active.security_id, day)
            if trigger_quote is not None and float(trigger_quote["close"]) > 0 and float(trigger_quote["volume"]) > 0 and float(trigger_quote["close"]) >= entry_premium * float(profit_multiple):
                pending_profit_since = pending_profit_since or day

        if action:
            actual_request = profit_request_date if action == "close_profit_restrike" else request_date
            trades.append(
                {
                    "candidate": label,
                    "signal_eval_date": latest_eval,
                    "scheduled_execution_date": actual_request,
                    "actual_execution_date": day,
                    "action": action,
                    "risk_tier": latest_tier,
                    "target_delta": latest_target,
                    "target_delta_error": target_error,
                    "entry_abs_delta": entry_delta,
                    "delta_source": delta_source,
                    "old_notional_fraction": old_notional,
                    "new_notional_fraction": active.notional_fraction if active else 0.0,
                    "old_contract": old.contract_id if old else "",
                    "new_contract": active.contract_id if active else "",
                    "old_month": old.contract_month if old else pd.NaT,
                    "new_month": active.contract_month if active else pd.NaT,
                    "new_strike": active.strike if active else np.nan,
                    "new_entry_moneyness": active.entry_moneyness if active else np.nan,
                    "delay_days": int((day - actual_request).days),
                    "delay_trading_days": _trading_delay(
                        trade_dates, actual_request, day
                    ),
                    "forced_month_roll": action == "close_roll_monthly",
                    "roll_request_date": actual_request
                    if action == "close_roll_monthly"
                    else pd.NaT,
                    "same_month_reset": bool(
                        old is not None
                        and active is not None
                        and old.contract_month == active.contract_month
                    ),
                }
            )

        expired = False
        if active is not None and active.expiry == day:
            expired = True
            active = None
            entry_premium = np.nan
            pending_profit_since = None
            pending_action_since = day
        mark_fraction = 0.0
        contract = ""
        qty = 0
        entry_moneyness = np.nan
        notional_fraction = 0.0
        effective_delta = 0.0
        absolute_delta = np.nan
        mark_delta_source = ""
        if active is not None:
            market_row = market_lookup.loc[day]
            current_delta, sigma, mark_delta_source = real_delta_from_mark(
                active.prior_mark,
                etf_close,
                active.strike,
                active.expiry,
                day,
                float(market_row["rate_close"]),
                float(market_row["dividend_close"]),
                float(market_row["sigma_close"]),
            )
            active.last_iv = sigma
            absolute_delta = abs(current_delta)
            notional_fraction = (
                active.qty
                * OPTION_MULTIPLIER
                * etf_close
                / (float(row.settle) * IC_MULTIPLIER)
            )
            effective_delta = notional_fraction * absolute_delta
            mark_fraction = (
                active.qty
                * OPTION_MULTIPLIER
                * active.prior_mark
                / (float(row.settle) * IC_MULTIPLIER)
            )
            contract = active.contract_id
            qty = active.qty
            entry_moneyness = active.entry_moneyness
        rows.append(
            {
                "date": day,
                "candidate": label,
                "put_pnl_ret": pnl,
                "put_cost_rate": cost,
                "put_mark_fraction": mark_fraction,
                "put_contract": contract,
                "put_qty": qty,
                "target_fraction": latest_target,
                "signal_target_fraction": latest_target,
                "target_delta": latest_target,
                "risk_tier": latest_tier,
                "actual_notional_fraction": notional_fraction,
                "abs_put_delta": absolute_delta,
                "effective_delta_hedge_ratio": effective_delta,
                "delta_source": mark_delta_source,
                "entry_moneyness_mark": entry_moneyness,
                "carried_mark": carried,
                "mark_stale_days": stale_days,
                "deferred_adjustment": bool(
                    pending_action_since is not None or pending_roll_since is not None
                ),
                "expired": expired,
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(trades)
