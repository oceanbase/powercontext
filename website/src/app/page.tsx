import Link from 'next/link';
import { buttonVariants } from 'fumadocs-ui/components/ui/button';
import { BrandLogo } from '@/components/brand-logo';

export default function LocaleChooser() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center px-6 text-center">
      <BrandLogo className="w-64" priority />
      <h1 className="mt-8 text-sm font-medium text-fd-muted-foreground">Choose a language / 选择语言</h1>
      <div className="mt-6 flex gap-3">
        <Link className={buttonVariants({ variant: 'outline' })} href="/en">English</Link>
        <Link className={buttonVariants({ variant: 'outline' })} href="/zh">简体中文</Link>
      </div>
    </main>
  );
}
