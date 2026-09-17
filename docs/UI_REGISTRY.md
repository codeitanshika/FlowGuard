# FlowGuard UI Registry

Design tokens and component conventions for the operator dashboard
(`dashboard/`). All tokens are CSS custom properties defined at `:root` and
consumed throughout the dashboard — no hardcoded colors, spacing, or font
sizes in component styles.

## Color Tokens

```css
:root {
  /* Surfaces */
  --color-surface: #ffffff;
  --color-surface-raised: #f7f8fa;
  --color-surface-sunken: #eef0f3;
  --color-border: #e2e5ea;

  /* Text */
  --color-text-primary: #111418;
  --color-text-secondary: #5b6472;
  --color-text-muted: #8a93a2;

  /* Brand / accent */
  --color-accent: #3b5bfd;
  --color-accent-hover: #2f49d6;

  /* Status */
  --color-status-healthy: #1f9d55;
  --color-status-degraded: #d68a1c;
  --color-status-down: #d13b3b;
  --color-status-unknown: #8a93a2;

  /* Status backgrounds (for badges/cards) */
  --color-status-healthy-bg: #e8f7ee;
  --color-status-degraded-bg: #fdf3e3;
  --color-status-down-bg: #fbe9e9;
  --color-status-unknown-bg: #f0f1f3;
}
```

Dark-mode overrides (if/when added) should redefine the same token names
under a `[data-theme="dark"]` selector rather than introducing new ones.

## Typography Tokens

```css
:root {
  --font-family-base: "Inter", system-ui, sans-serif;
  --font-family-mono: "JetBrains Mono", ui-monospace, monospace;

  --font-size-xs: 0.75rem;   /* 12px — labels, timestamps */
  --font-size-sm: 0.875rem;  /* 14px — body text, table cells */
  --font-size-md: 1rem;      /* 16px — default body */
  --font-size-lg: 1.25rem;   /* 20px — card titles */
  --font-size-xl: 1.75rem;   /* 28px — page titles */

  --font-weight-regular: 400;
  --font-weight-medium: 500;
  --font-weight-bold: 700;

  --line-height-tight: 1.25;
  --line-height-normal: 1.5;
}
```

- Numeric/trace data (transaction IDs, latencies, trace IDs) always uses
  `--font-family-mono`.
- Page and section titles use `--font-weight-bold`; card/table labels use
  `--font-weight-medium`.

## Spacing Scale

```css
:root {
  --space-1: 0.25rem;  /* 4px */
  --space-2: 0.5rem;   /* 8px */
  --space-3: 0.75rem;  /* 12px */
  --space-4: 1rem;     /* 16px */
  --space-5: 1.5rem;   /* 24px */
  --space-6: 2rem;     /* 32px */
  --space-8: 3rem;     /* 48px */
}
```

Use the scale for all margin/padding/gap values — no arbitrary pixel values
in component CSS. Card padding defaults to `--space-4`; section gaps default
to `--space-6`.

## Component Patterns

### Cards
The base container for grouped info (a service's health card, an agent's
activity card).

```css
.card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 0.5rem;
  padding: var(--space-4);
}

.card-title {
  font-size: var(--font-size-lg);
  font-weight: var(--font-weight-bold);
  margin-bottom: var(--space-3);
}
```

### Badges
Used for status labels (service health, breaker state, transaction status).
Badge color pairs always use a `-bg` token with its matching solid text
color — never a solid background with white text, to keep contrast
consistent across statuses.

```css
.badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-1) var(--space-2);
  border-radius: 999px;
  font-size: var(--font-size-xs);
  font-weight: var(--font-weight-medium);
}

.badge--healthy  { background: var(--color-status-healthy-bg);  color: var(--color-status-healthy); }
.badge--degraded { background: var(--color-status-degraded-bg); color: var(--color-status-degraded); }
.badge--down      { background: var(--color-status-down-bg);      color: var(--color-status-down); }
.badge--unknown    { background: var(--color-status-unknown-bg);    color: var(--color-status-unknown); }
```

### Tables
Used for transaction lists and agent action logs.

```css
.table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--font-size-sm);
}

.table th {
  text-align: left;
  color: var(--color-text-secondary);
  font-weight: var(--font-weight-medium);
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--color-border);
}

.table td {
  padding: var(--space-3);
  border-bottom: 1px solid var(--color-border);
  color: var(--color-text-primary);
}
```

Monetary and trace-ID columns use `--font-family-mono`; every row
representing a transaction includes a status badge in its first or last
column, never color alone, to convey state.

### Status Indicators
A compact dot + label used in the health grid, distinct from a badge (badges
label a single item; status indicators summarize a whole service at a
glance).

```css
.status-dot {
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 50%;
  display: inline-block;
}

.status-dot--healthy  { background: var(--color-status-healthy); }
.status-dot--degraded { background: var(--color-status-degraded); }
.status-dot--down      { background: var(--color-status-down); }
.status-dot--unknown    { background: var(--color-status-unknown); }
```

Status dots pulse (a subtle CSS animation) only in the `--down` state, to
draw the eye without relying on color perception alone.

## Dashboard Layout Rules

- The page is a single-column layout on narrow viewports, three-zone grid on
  wide viewports: **health grid** (top), **agent activity feed** (left,
  scrollable), **traces/transactions panel** (right, scrollable).
- The health grid always renders all four services in a fixed order
  (`payment`, `fraud`, `user`, `notification`) so operators build muscle
  memory for card position — never re-sort by status.
- The agent activity feed is newest-first and each entry shows: timestamp,
  agent name, the triggering condition, and the action taken — using the
  same badge component as elsewhere for the action's resulting status.
- Circuit breaker state (closed/open/half-open) is shown directly on each
  service's health card, not buried in a separate panel — it's the single
  most important resilience signal on the page.
- The fault-injection control panel is visually separated (a distinct
  bordered section, not a card) and requires a confirmation step before
  triggering, since it deliberately breaks something.
- All spacing in the layout grid uses the spacing scale (`--space-*`); the
  outer page padding is `--space-6` on desktop, `--space-4` on mobile.
