export function localizedPath(path: string, isZh: boolean): string {
  if (
    !isZh ||
    !path.startsWith('/') ||
    path.startsWith('//') ||
    path === '/zh' ||
    path.startsWith('/zh/') ||
    path.startsWith('/zh?') ||
    path.startsWith('/zh#')
  ) {
    return path;
  }

  return path === '/' ? '/zh/' : `/zh${path}`;
}
