import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEventHandler,
  type ReactNode,
} from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import {mergeSearchStrings, useHistorySelector} from '@docusaurus/theme-common';
import {useAlternatePageUtils} from '@docusaurus/theme-common/internal';
import styles from './styles.module.css';

type Props = {
  className?: string;
  mobile?: boolean;
  onClick?: MouseEventHandler<HTMLAnchorElement>;
};

type LocaleOption = {
  locale: string;
  label: string;
  lang?: string;
  to: string;
  active: boolean;
};

function GlobeIcon() {
  return (
    <svg
      aria-hidden="true"
      className={styles.icon}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.8" />
      <path
        d="M3.5 12h17M12 3c2.1 2.4 3.15 5.4 3.15 9S14.1 18.6 12 21c-2.1-2.4-3.15-5.4-3.15-9S9.9 5.4 12 3Z"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg
      aria-hidden="true"
      className={styles.checkIcon}
      viewBox="0 0 20 20"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path
        d="m4.75 10.25 3.35 3.35 7.15-7.2"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
    </svg>
  );
}

function getLocaleLabel(locale: string, fallbackLabel?: string) {
  if (locale === 'en') {
    return 'English';
  }
  if (locale === 'zh') {
    return '简体中文';
  }
  return fallbackLabel ?? locale;
}

function getLocaleCode(locale: string) {
  if (locale === 'en') {
    return 'EN';
  }
  if (locale === 'zh') {
    return '中';
  }
  return locale.slice(0, 2).toUpperCase();
}

export default function LocaleSwitchNavbarItem({
  className,
  mobile = false,
  onClick,
}: Props): ReactNode {
  const [open, setOpen] = useState(false);
  const dropdownRef = useRef<HTMLElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const {
    siteConfig,
    i18n: {currentLocale, localeConfigs, locales},
  } = useDocusaurusContext();
  const alternatePageUtils = useAlternatePageUtils();
  const search = useHistorySelector((history) => history.location.search);
  const hash = useHistorySelector((history) => history.location.hash);

  const localeOptions = useMemo((): LocaleOption[] => {
    return locales.map((locale) => {
      const localeConfig = localeConfigs[locale];
      const isSameDomain = localeConfig?.url === siteConfig.url;
      const localeBaseUrl = isSameDomain
        ? `pathname://${alternatePageUtils.createUrl({
            locale,
            fullyQualified: false,
          })}`
        : alternatePageUtils.createUrl({
            locale,
            fullyQualified: true,
          });

      return {
        locale,
        label: getLocaleLabel(locale, localeConfig?.label),
        lang: localeConfig?.htmlLang,
        to: `${localeBaseUrl}${mergeSearchStrings([search], 'append')}${hash}`,
        active: locale === currentLocale,
      };
    });
  }, [
    alternatePageUtils,
    currentLocale,
    hash,
    localeConfigs,
    locales,
    search,
    siteConfig.url,
  ]);

  const currentLabel =
    localeOptions.find((option) => option.active)?.label ?? currentLocale;
  const languageButtonLabel =
    currentLocale === 'zh'
      ? `选择语言，当前为${currentLabel}`
      : `Select language, current language is ${currentLabel}`;

  const toggleOpen = useCallback(() => {
    setOpen((current) => !current);
  }, []);

  const closeDropdown = useCallback(() => {
    setOpen(false);
  }, []);

  const setDropdownNode = useCallback((node: HTMLElement | null) => {
    dropdownRef.current = node;
  }, []);

  useEffect(() => {
    if (!open) {
      return undefined;
    }

    const handleClickOutside = (
      event: MouseEvent | TouchEvent | FocusEvent,
    ) => {
      if (
        !dropdownRef.current ||
        dropdownRef.current.contains(event.target as Node)
      ) {
        return;
      }
      closeDropdown();
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        closeDropdown();
        triggerRef.current?.focus();
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('touchstart', handleClickOutside);
    document.addEventListener('focusin', handleClickOutside);
    document.addEventListener('keydown', handleKeyDown);

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('touchstart', handleClickOutside);
      document.removeEventListener('focusin', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [closeDropdown, open]);

  const handleLocaleClick: MouseEventHandler<HTMLAnchorElement> = (event) => {
    closeDropdown();
    onClick?.(event);
  };

  const localeMenu = (
    <ul
      className={mobile ? styles.mobileMenu : styles.localeMenu}
      aria-label={currentLocale === 'zh' ? '语言选项' : 'Language options'}
    >
      {localeOptions.map((option) => (
        <li key={option.locale} className={styles.localeMenuItem}>
          <Link
            lang={option.lang}
            className={clsx(
              mobile ? 'menu__link' : styles.localeMenuLink,
              option.active &&
                (mobile ? 'menu__link--active' : styles.localeMenuLinkActive),
            )}
            to={option.to}
            target="_self"
            autoAddBaseUrl={false}
            aria-current={option.active ? 'page' : undefined}
            onClick={handleLocaleClick}
          >
            <span className={styles.localeCode} aria-hidden="true">
              {getLocaleCode(option.locale)}
            </span>
            <span className={styles.localeName}>{option.label}</span>
            <span className={styles.checkMark} aria-hidden="true">
              {option.active && <CheckIcon />}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );

  if (mobile) {
    return (
      <li
        ref={setDropdownNode}
        className={clsx('menu__list-item', styles.mobileLocaleItem)}
      >
        <button
          ref={triggerRef}
          type="button"
          className={clsx(
            'clean-btn menu__link',
            styles.mobileToggle,
            className,
          )}
          aria-expanded={open}
          aria-label={languageButtonLabel}
          onClick={toggleOpen}
        >
          <GlobeIcon />
          <span>{currentLocale === 'zh' ? '语言' : 'Language'}</span>
        </button>
        {open && localeMenu}
      </li>
    );
  }

  return (
    <div
      ref={setDropdownNode}
      className={clsx('navbar__item', styles.localeDropdown, className)}
    >
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={open}
        aria-label={languageButtonLabel}
        className={clsx(styles.localeSwitch, open && styles.localeSwitchOpen)}
        title={languageButtonLabel}
        onClick={toggleOpen}
      >
        <GlobeIcon />
      </button>
      {open && localeMenu}
    </div>
  );
}
