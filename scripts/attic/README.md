# Attic — quarantined one-off senders (2026-07-13)

Dead, unscheduled scripts that POST to the Resend API with no unified send
gate (kill_switches.is_send_allowed). Moved here during the 2026-07-13 audit
repair so an agent told to "run the blast script" can't fire ungated email.
Restore deliberately, adding the gate first (see drip-sender.py recipient_gate).
