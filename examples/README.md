# Examples

Ready-to-paste Home Assistant snippets for the Colis Privé integration.

| Folder | Contents |
|---|---|
| [`automations/`](automations/) | YAML automations — copy them into your `automations.yaml` or paste them into the Automation editor in **raw editor** mode. |
| [`dashboards/`](dashboards/) | Lovelace snippets, including [`add_parcel_card.yaml`](dashboards/add_parcel_card.yaml) — track a new parcel straight from a dashboard via the `colis_prive.track_parcel` service. |

Entity IDs depend on your hub's name (`Colis Privé (<postal code>)`); adjust them to match yours. With more than one hub, the services also need `country` and `postal_code`.

**Feeding Colis Privé from e-mail:** Colis Privé is code-based — every parcel must be registered by its tracking code before it can be tracked. [`automations/track_parcels_from_email.yaml`](automations/track_parcels_from_email.yaml) extracts tracking codes from incoming shipping mails (core IMAP integration + regex, with an optional AI fallback) and registers them automatically; setup guide and pitfalls in [`automations/track_parcels_from_email.md`](automations/track_parcels_from_email.md).

## Services

| Service | Description |
|---|---|
| `colis_prive.track_parcel` | Start tracking a parcel (`tracking_code`; `country` and `postal_code` pick the hub and are required with more than one hub). |
| `colis_prive.untrack_parcel` | Stop tracking a parcel (same fields; required only when the code is tracked in more than one hub). |

## Events used in the examples

The coordinator fires these on the HA event bus:

| Event | When | Payload |
|---|---|---|
| `colis_prive_parcel_registered` | A new parcel appears in the active list | The full normalised parcel dict |
| `colis_prive_parcel_status_changed` | A parcel's canonical status changes | Same, plus `old_status` / `new_status` |
| `colis_prive_parcel_delivered` | A parcel reaches the delivered status | Same, plus `old_status` / `new_status` (fires *instead of* `status_changed` on that final hop) |

Colis Privé publishes no expected delivery time, so `colis_prive_parcel_delivery_time_changed` never fires. Events are suppressed on the first refresh after start-up.
