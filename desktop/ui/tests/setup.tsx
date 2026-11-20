import {
  Children,
  cloneElement,
  createContext,
  Fragment,
  isValidElement,
  useContext,
  useMemo,
  useState,
  type ComponentProps,
  type ReactNode,
  type ReactElement,
} from "react";
import { vi } from "vitest";

// Business tests exercise value changes and menu actions with lightweight DOM
// controls. Real Radix positioning, focus, and portal behavior is covered by the
// Dialog/Tooltip component tests and browser regression rather than jsdom.

if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false;
}

if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = () => undefined;
}

if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => undefined;
}

if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => undefined;
}

type TestOption = {
  disabled: boolean;
  label: string;
  value: string;
};

function textContent(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (!isValidElement<{ children?: ReactNode }>(node)) return "";
  return Children.toArray(node.props.children).map(textContent).join("");
}

function collectOptions(node: ReactNode, result: TestOption[] = []) {
  Children.forEach(node, (child) => {
    if (
      !isValidElement<{
        children?: ReactNode;
        disabled?: boolean;
        value?: string;
      }>(child)
    )
      return;
    if (typeof child.props.value === "string") {
      result.push({
        disabled: !!child.props.disabled,
        label: textContent(child.props.children),
        value: child.props.value,
      });
    }
    collectOptions(child.props.children, result);
  });
  return result;
}

type SelectContextValue = {
  onValueChange?: (value: string) => void;
  options: TestOption[];
  value: string;
};

const TestSelectContext = createContext<SelectContextValue>({
  options: [],
  value: "",
});

type MenuContextValue = {
  close: () => void;
  open: boolean;
  setOpen: (open: boolean) => void;
};

const TestMenuContext = createContext<MenuContextValue>({
  close: () => undefined,
  open: false,
  setOpen: () => undefined,
});

const TestRadioGroupContext = createContext<{
  onValueChange?: (value: string) => void;
  value?: string;
}>({});

vi.mock("radix-ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("radix-ui")>();

  function Root({
    children,
    defaultValue = "",
    onValueChange,
    value,
  }: ComponentProps<(typeof actual.Select)["Root"]>) {
    const [internalValue, setInternalValue] = useState(defaultValue);
    const currentValue = value ?? internalValue;
    const options = useMemo(() => collectOptions(children), [children]);
    return (
      <TestSelectContext.Provider
        value={{
          options,
          value: currentValue,
          onValueChange: (nextValue) => {
            if (value === undefined) setInternalValue(nextValue);
            onValueChange?.(nextValue);
          },
        }}
      >
        {children}
      </TestSelectContext.Provider>
    );
  }

  function Trigger({
    children: _children,
    className: _className,
    ...props
  }: ComponentProps<(typeof actual.Select)["Trigger"]>) {
    const context = useContext(TestSelectContext);
    return (
      <select
        aria-label={props["aria-label"]}
        disabled={props.disabled}
        id={props.id}
        value={context.value}
        onChange={(event) => context.onValueChange?.(event.target.value)}
      >
        {context.value === "" &&
        !context.options.some((option) => option.value === "") ? (
          <option value="" />
        ) : null}
        {context.options.map((option) => (
          <option
            key={option.value}
            disabled={option.disabled}
            value={option.value}
          >
            {option.label || option.value}
          </option>
        ))}
      </select>
    );
  }

  const PassThrough = ({ children }: { children?: ReactNode }) => (
    <Fragment>{children}</Fragment>
  );
  const Empty = () => null;

  function MenuRoot({
    children,
    onOpenChange,
    open,
  }: ComponentProps<(typeof actual.DropdownMenu)["Root"]>) {
    const [internalOpen, setInternalOpen] = useState(false);
    const currentOpen = open ?? internalOpen;
    const setOpen = (nextOpen: boolean) => {
      if (open === undefined) setInternalOpen(nextOpen);
      onOpenChange?.(nextOpen);
    };
    return (
      <TestMenuContext.Provider
        value={{
          close: () => setOpen(false),
          open: currentOpen,
          setOpen,
        }}
      >
        {children}
      </TestMenuContext.Provider>
    );
  }

  function MenuTrigger({
    asChild,
    children,
    ...props
  }: ComponentProps<(typeof actual.DropdownMenu)["Trigger"]>) {
    const menu = useContext(TestMenuContext);
    const triggerProps = {
      "aria-expanded": menu.open,
      "aria-haspopup": "menu" as const,
      onClick: () => menu.setOpen(!menu.open),
    };
    if (asChild && isValidElement(children)) {
      return cloneElement(children as ReactElement, triggerProps);
    }
    return (
      <button type="button" {...props} {...triggerProps}>
        {children}
      </button>
    );
  }

  function MenuContent({
    children,
  }: ComponentProps<(typeof actual.DropdownMenu)["Content"]>) {
    const menu = useContext(TestMenuContext);
    return menu.open ? <div role="menu">{children}</div> : null;
  }

  function MenuItem({
    children,
    disabled,
    onSelect,
  }: ComponentProps<(typeof actual.DropdownMenu)["Item"]>) {
    const menu = useContext(TestMenuContext);
    return (
      <button
        type="button"
        role="menuitem"
        disabled={disabled}
        onClick={(event) => {
          onSelect?.(event as never);
          menu.close();
        }}
      >
        {children}
      </button>
    );
  }

  function RadioGroup({
    children,
    onValueChange,
    value,
  }: ComponentProps<(typeof actual.DropdownMenu)["RadioGroup"]>) {
    return (
      <TestRadioGroupContext.Provider value={{ onValueChange, value }}>
        {children}
      </TestRadioGroupContext.Provider>
    );
  }

  function RadioItem({
    children,
    disabled,
    value,
  }: ComponentProps<(typeof actual.DropdownMenu)["RadioItem"]>) {
    const menu = useContext(TestMenuContext);
    const group = useContext(TestRadioGroupContext);
    return (
      <button
        type="button"
        role="menuitemradio"
        aria-checked={group.value === value}
        disabled={disabled}
        onClick={() => {
          group.onValueChange?.(value);
          menu.close();
        }}
      >
        {children}
      </button>
    );
  }

  return {
    ...actual,
    Select: {
      ...actual.Select,
      Content: Empty,
      Group: PassThrough,
      Icon: Empty,
      Item: Empty,
      ItemIndicator: Empty,
      ItemText: PassThrough,
      Label: PassThrough,
      Portal: PassThrough,
      Root,
      Trigger,
      Value: Empty,
      Viewport: PassThrough,
    },
    DropdownMenu: {
      ...actual.DropdownMenu,
      Content: MenuContent,
      Group: PassThrough,
      Item: MenuItem,
      ItemIndicator: Empty,
      Label: PassThrough,
      Portal: PassThrough,
      RadioGroup,
      RadioItem,
      Root: MenuRoot,
      Separator: () => <hr />,
      Trigger: MenuTrigger,
    },
  };
});
