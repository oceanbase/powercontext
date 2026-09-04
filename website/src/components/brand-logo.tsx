type BrandLogoProps = {
  className?: string;
  priority?: boolean;
};

export function BrandLogo({ className, priority = false }: BrandLogoProps) {
  return (
    <span aria-label="PowerContext" className={['block leading-none', className].filter(Boolean).join(' ')} role="img">
      <img
        alt=""
        className="h-auto w-full dark:hidden"
        decoding="async"
        fetchPriority={priority ? 'high' : 'auto'}
        height={240}
        src="/powercontext-color.png"
        width={1696}
      />
      <img
        alt=""
        className="hidden h-auto w-full dark:block"
        decoding="async"
        fetchPriority={priority ? 'high' : 'auto'}
        height={240}
        src="/powercontext-reverse.png"
        width={1696}
      />
    </span>
  );
}
