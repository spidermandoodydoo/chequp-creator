/**
 * CheqUp Tailwind preset (Tailwind v3).
 *
 * Every value here is a reference to a CSS custom property declared in styles.css — the
 * preset does NOT copy hex values. That matters for two reasons:
 *
 *   1. The Figma variables stay the single source of truth. Regenerate this file when the
 *      .fig changes; nothing is transcribed by hand.
 *   2. The theme tier keeps working. Because 'accent' resolves to var(--theme-accent),
 *      putting data-mode="women-s-health" on any ancestor recolours every bg-accent in
 *      that subtree with no config change.
 *
 * Usage — tailwind.config.js:
 *
 *   module.exports = {
 *     presets: [require('@chequp/tokens/tailwind.preset.cjs')],
 *     content: ['./src/**' + '/*.{vue,ts,tsx}']
 *   };
 *
 * And import the stylesheet once at your app entry, so the custom properties exist:
 *
 *   import '@chequp/tokens/styles.css';
 *
 * Colour utilities are generated for the primitive and semantic tiers only. Component
 * tokens (--button-*, --field-*) are deliberately excluded: those belong to the
 * components, not to product code.
 */
module.exports = {
  theme: {
    extend: {
      colors: {
      'brand-dark-sand': 'var(--brand-dark-sand)',
      'brand-grey-disabled-quiet': 'var(--brand-grey-disabled-quiet)',
      'brand-grey-disabled': 'var(--brand-grey-disabled)',
      'brand-horizon-accent': 'var(--brand-horizon-accent)',
      'brand-horizon': 'var(--brand-horizon)',
      'brand-lavender-active': 'var(--brand-lavender-active)',
      'brand-lavender-hover': 'var(--brand-lavender-hover)',
      'brand-lavender-tint': 'var(--brand-lavender-tint)',
      'brand-lavender': 'var(--brand-lavender)',
      'brand-midnight': 'var(--brand-midnight)',
      'brand-mulberry-accent': 'var(--brand-mulberry-accent)',
      'brand-mulberry': 'var(--brand-mulberry)',
      'brand-orchid-accent': 'var(--brand-orchid-accent)',
      'brand-orchid': 'var(--brand-orchid)',
      'brand-purple-active': 'var(--brand-purple-active)',
      'brand-purple-hover': 'var(--brand-purple-hover)',
      'brand-purple': 'var(--brand-purple)',
      'brand-sand-disabled': 'var(--brand-sand-disabled)',
      'brand-sand-tint': 'var(--brand-sand-tint)',
      'brand-sand': 'var(--brand-sand)',
      'brand-sunflower': 'var(--brand-sunflower)',
      'brand-tangerine-accent': 'var(--brand-tangerine-accent)',
      'brand-tangerine': 'var(--brand-tangerine)',
      'brand-white': 'var(--brand-white)',
      'brand-willow-accent': 'var(--brand-willow-accent)',
      'brand-willow': 'var(--brand-willow)',
      'accent-default': 'var(--color-accent-default)',
      'border-control-hover': 'var(--color-border-control-hover)',
      'border-control-quiet': 'var(--color-border-control-quiet)',
      'border-control': 'var(--color-border-control)',
      'border-inverse-subtle': 'var(--color-border-inverse-subtle)',
      'border-inverse': 'var(--color-border-inverse)',
      'border-subtle': 'var(--color-border-subtle)',
      'category-horizon-active': 'var(--color-category-horizon-active)',
      'category-horizon-hover': 'var(--color-category-horizon-hover)',
      'category-horizon-light': 'var(--color-category-horizon-light)',
      'category-horizon': 'var(--color-category-horizon)',
      'category-mulberry-active': 'var(--color-category-mulberry-active)',
      'category-mulberry-hover': 'var(--color-category-mulberry-hover)',
      'category-mulberry-light': 'var(--color-category-mulberry-light)',
      'category-mulberry': 'var(--color-category-mulberry)',
      'category-orchid-active': 'var(--color-category-orchid-active)',
      'category-orchid-hover': 'var(--color-category-orchid-hover)',
      'category-orchid-light': 'var(--color-category-orchid-light)',
      'category-orchid': 'var(--color-category-orchid)',
      'category-tangerine-active': 'var(--color-category-tangerine-active)',
      'category-tangerine-hover': 'var(--color-category-tangerine-hover)',
      'category-tangerine-light': 'var(--color-category-tangerine-light)',
      'category-tangerine': 'var(--color-category-tangerine)',
      'category-willow-active': 'var(--color-category-willow-active)',
      'category-willow-hover': 'var(--color-category-willow-hover)',
      'category-willow-light': 'var(--color-category-willow-light)',
      'category-willow': 'var(--color-category-willow)',
      'feedback-error': 'var(--color-feedback-error)',
      'feedback-info': 'var(--color-feedback-info)',
      'feedback-success': 'var(--color-feedback-success)',
      'feedback-warning': 'var(--color-feedback-warning)',
      'focus-ring': 'var(--color-focus-ring)',
      'interactive-active': 'var(--color-interactive-active)',
      'interactive-hover': 'var(--color-interactive-hover)',
      'interactive-selected': 'var(--color-interactive-selected)',
      'on-primary-08': 'var(--color-on-primary-08)',
      'on-primary-12': 'var(--color-on-primary-12)',
      'on-primary-16': 'var(--color-on-primary-16)',
      'on-primary-24': 'var(--color-on-primary-24)',
      'on-primary-35': 'var(--color-on-primary-35)',
      'on-primary-40': 'var(--color-on-primary-40)',
      'on-primary-62': 'var(--color-on-primary-62)',
      'on-primary-64': 'var(--color-on-primary-64)',
      'on-primary-72': 'var(--color-on-primary-72)',
      'on-primary-88': 'var(--color-on-primary-88)',
      'on-primary-92': 'var(--color-on-primary-92)',
      'on-primary-base': 'var(--color-on-primary-base)',
      'primary-active': 'var(--color-primary-active)',
      'primary-alpha-14': 'var(--color-primary-alpha-14)',
      'primary-alpha-20': 'var(--color-primary-alpha-20)',
      'primary-alpha-24': 'var(--color-primary-alpha-24)',
      'primary-alpha-28': 'var(--color-primary-alpha-28)',
      'primary-alpha-56': 'var(--color-primary-alpha-56)',
      'primary-base': 'var(--color-primary-base)',
      'primary-dark-03': 'var(--color-primary-dark-03)',
      'primary-dark-04': 'var(--color-primary-dark-04)',
      'primary-dark-06': 'var(--color-primary-dark-06)',
      'primary-dark-07': 'var(--color-primary-dark-07)',
      'primary-dark-08': 'var(--color-primary-dark-08)',
      'primary-dark-10': 'var(--color-primary-dark-10)',
      'primary-dark-11': 'var(--color-primary-dark-11)',
      'primary-dark-12': 'var(--color-primary-dark-12)',
      'primary-dark-16': 'var(--color-primary-dark-16)',
      'primary-dark-20': 'var(--color-primary-dark-20)',
      'primary-dark-24': 'var(--color-primary-dark-24)',
      'primary-dark-32': 'var(--color-primary-dark-32)',
      'primary-dark-40': 'var(--color-primary-dark-40)',
      'primary-dark-48': 'var(--color-primary-dark-48)',
      'primary-dark-50': 'var(--color-primary-dark-50)',
      'primary-dark-60': 'var(--color-primary-dark-60)',
      'primary-dark-62': 'var(--color-primary-dark-62)',
      'primary-dark-64': 'var(--color-primary-dark-64)',
      'primary-dark-68': 'var(--color-primary-dark-68)',
      'primary-dark-70': 'var(--color-primary-dark-70)',
      'primary-dark-72': 'var(--color-primary-dark-72)',
      'primary-dark-76': 'var(--color-primary-dark-76)',
      'primary-dark-78': 'var(--color-primary-dark-78)',
      'primary-dark': 'var(--color-primary-dark)',
      'primary-hover': 'var(--color-primary-hover)',
      'primary-light-active': 'var(--color-primary-light-active)',
      'primary-light-hover': 'var(--color-primary-light-hover)',
      'primary-light': 'var(--color-primary-light)',
      'surface-disabled-fill': 'var(--color-surface-disabled-fill)',
      'surface-disabled-quiet': 'var(--color-surface-disabled-quiet)',
      'surface-disabled': 'var(--color-surface-disabled)',
      'surface-primary': 'var(--color-surface-primary)',
      'surface-secondary': 'var(--color-surface-secondary)',
      'surface-selected': 'var(--color-surface-selected)',
      'surface-tertiary': 'var(--color-surface-tertiary)',
      'surface-tint': 'var(--color-surface-tint)',
      'system-error-32': 'var(--color-system-error-32)',
      'system-error-deep': 'var(--color-system-error-deep)',
      'system-error-tint': 'var(--color-system-error-tint)',
      'system-info-32': 'var(--color-system-info-32)',
      'system-info-deep': 'var(--color-system-info-deep)',
      'system-info-tint': 'var(--color-system-info-tint)',
      'system-success-32': 'var(--color-system-success-32)',
      'system-success-deep': 'var(--color-system-success-deep)',
      'system-success-tint': 'var(--color-system-success-tint)',
      'system-text': 'var(--color-system-text)',
      'system-warning-32': 'var(--color-system-warning-32)',
      'system-warning-deep': 'var(--color-system-warning-deep)',
      'system-warning-tint': 'var(--color-system-warning-tint)',
      'text-disabled': 'var(--color-text-disabled)',
      'text-inverse-secondary': 'var(--color-text-inverse-secondary)',
      'text-inverse': 'var(--color-text-inverse)',
      'text-muted': 'var(--color-text-muted)',
      'text-primary': 'var(--color-text-primary)',
      'text-secondary': 'var(--color-text-secondary)',
      'yellow-wash': 'var(--color-yellow-wash)',
      'accent-active': 'var(--theme-accent-active)',
      'accent-hover': 'var(--theme-accent-hover)',
      'accent-light': 'var(--theme-accent-light)',
      'accent': 'var(--theme-accent)'
      },
      borderRadius: {
      'bar': 'calc(var(--radius-bar) * 1px)',
      'chip': 'calc(var(--radius-chip) * 1px)',
      'full': 'calc(var(--radius-full) * 1px)',
      'indicator': 'calc(var(--radius-indicator) * 1px)',
      'lg': 'calc(var(--radius-lg) * 1px)',
      'md': 'calc(var(--radius-md) * 1px)',
      'panel': 'calc(var(--radius-panel) * 1px)',
      'sm': 'calc(var(--radius-sm) * 1px)',
      'xl': 'calc(var(--radius-xl) * 1px)',
      'xs': 'calc(var(--radius-xs) * 1px)',
      'xxs': 'calc(var(--radius-xxs) * 1px)'
      },
      spacing: {
      '2xl': 'calc(var(--spacing-2xl) * 1px)',
      '2xs': 'calc(var(--spacing-2xs) * 1px)',
      '3xl': 'calc(var(--spacing-3xl) * 1px)',
      '3xs': 'calc(var(--spacing-3xs) * 1px)',
      '4xl': 'calc(var(--spacing-4xl) * 1px)',
      '5xl': 'calc(var(--spacing-5xl) * 1px)',
      'control-height-icon': 'calc(var(--spacing-control-height-icon) * 1px)',
      'control-height': 'calc(var(--spacing-control-height) * 1px)',
      'control-padding-x': 'calc(var(--spacing-control-padding-x) * 1px)',
      'gutter': 'calc(var(--spacing-gutter) * 1px)',
      'lg': 'calc(var(--spacing-lg) * 1px)',
      'md': 'calc(var(--spacing-md) * 1px)',
      'page-margin': 'calc(var(--spacing-page-margin) * 1px)',
      'section': 'calc(var(--spacing-section) * 1px)',
      'sm': 'calc(var(--spacing-sm) * 1px)',
      'xl': 'calc(var(--spacing-xl) * 1px)',
      'xs': 'calc(var(--spacing-xs) * 1px)'
      },
      fontSize: {
      'board-intro': 'calc(var(--type-board-intro-size) * 1px)',
      'board-section': 'calc(var(--type-board-section-size) * 1px)',
      'board-title': 'calc(var(--type-board-title-size) * 1px)',
      'body-emphasis': 'calc(var(--type-body-emphasis-size) * 1px)',
      'body-lg': 'calc(var(--type-body-lg-size) * 1px)',
      'body': 'calc(var(--type-body-size) * 1px)',
      'body-sm': 'calc(var(--type-body-sm-size) * 1px)',
      'caption': 'calc(var(--type-caption-size) * 1px)',
      'display-1': 'calc(var(--type-display-1-size) * 1px)',
      'heading-1': 'calc(var(--type-heading-1-size) * 1px)',
      'heading-2': 'calc(var(--type-heading-2-size) * 1px)',
      'heading-3': 'calc(var(--type-heading-3-size) * 1px)',
      'heading-4': 'calc(var(--type-heading-4-size) * 1px)',
      'label-lg': 'calc(var(--type-label-lg-size) * 1px)',
      'label': 'calc(var(--type-label-size) * 1px)',
      'label-sm': 'calc(var(--type-label-sm-size) * 1px)',
      'quote': 'calc(var(--type-quote-size) * 1px)'
      },
      fontFamily: {
        core: ['var(--font-core)'],
        mono: ['var(--font-mono)']
      },
      lineHeight: {
        tight: 'var(--leading-tight)',
        snug: 'var(--leading-snug)',
        compact: 'var(--leading-compact)',
        normal: 'var(--leading-normal)',
        body: 'var(--leading-body)',
        relaxed: 'var(--leading-relaxed)'
      },
      letterSpacing: {
        tight: 'var(--tracking-tight)',
        label: 'var(--tracking-label)'
      },
      boxShadow: {
        'hairline-card': 'var(--hairline-card)',
        'hairline-table': 'var(--hairline-table)',
        'hairline-quiet': 'var(--hairline-quiet)',
        'hairline-field': 'var(--hairline-field)',
        'hairline-strong': 'var(--hairline-strong)',
        'ring-accent': 'var(--ring-accent)',
        'focus-glow': 'var(--focus-glow)'
      },
      transitionTimingFunction: {
        relief: 'var(--ease-relief)'
      },
      transitionDuration: {
        quick: 'calc(var(--motion-duration-quick) * 1ms)',
        settle: 'calc(var(--motion-duration-settle) * 1ms)'
      }
    }
  }
};
