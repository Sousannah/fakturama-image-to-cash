# Icon templates

Tier-3 grounding matches these small images *inside a rectangle that UIA
resolved at runtime*. They are the only binary part of the grounding strategy.

They are intentionally **not** committed pre-made: icon rendering depends on the
Fakturama version, the platform theme and the display scaling of the machine
that will run the automation. Capture them there instead:

```
f2c capture-icon --name select_contact --scope order_editor
```

That writes `_capture_select_contact.png` here. Crop the icon out of it (any
image editor; keep it tight, 16-24 px) and save it under the plain name.

Templates referenced by `selectors.yaml`:

| file                  | what it is                                              |
|-----------------------|---------------------------------------------------------|
| `select_contact.png`  | the upper existing-contact selector beside Addresses     |
| `select_product.png`  | the upper product selector beside the Items table        |
| `new_green_plus.png`  | the green + that creates a new record (never clicked on the Order) |
| `toolbar_order.png`   | the Order button in the top toolbar                      |
| `toolbar_save.png`    | the toolbar Save control                                 |

Every one of these has a UIA and/or OCR fallback in `selectors.yaml`, so a
missing template degrades the run rather than breaking it.
