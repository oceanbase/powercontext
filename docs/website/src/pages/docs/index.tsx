import {Redirect} from '@docusaurus/router';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import {localizedPath} from '../../utils/localizedPath';

export default function DocsRedirect() {
  const {i18n} = useDocusaurusContext();

  return (
    <Redirect
      to={localizedPath(
        '/docs/guides/getting_started',
        i18n.currentLocale === 'zh',
      )}
    />
  );
}
