# Independent server/display repair audit

Final result: no remaining confirmed defect in this scoped review. Seven independent tests passed (run02,3.71s). Reviewer did not author server/display changes or edit production files during this audit.

Review identified an incomplete S-V2-1 repair under contention: starting collection_clock after the shared lock omits time spent waiting for another query. The peer audit independently reproduced the original rejection. Server author moved observation context before lock acquisition and moved scheduled clock sampling into the executor worker. Our distinct test uses two real threads and the actual shared RLock (with lock-attempt notification); a two-second wait now produces observation16:00:02 and accepts a valid16:00:01 quote. First local run occurred after that followup landed; it is not presented as an original failing-run record.

Additional passing probes cover unreadable head, partial anchor installation, two concurrent coordinators with distinct heads and a cancelled first query, nested display-context cancellation, catchup crossing midnight to an unfinished session, and refresh cancellation suppressing further collection. Disposable ledger bytes remain unchanged.

Static review confirms display head remains distinct from recalculation predecessor, capture uses its own committed head under the shared lock, finally restores copied prior anchors, and exception/cancellation contexts reset. Cancelled asyncio.to_thread work that has already begun is not forcibly killed; this review does not claim that pre-existing limitation was eliminated. No live network, production ledger mutation, or remote deployment. Final cumulative/source-bound validation is separate.
