/** Title block every dashboard page opens with — kept generic (not
 * Coming-Soon-specific) so it's still the right component once these pages
 * grow real content. */
export function PageHeader({ title, sublabel }: { title: string; sublabel?: string }) {
  return (
    <div className="dash-page-header">
      <h1 className="dash-page-header__title">{title}</h1>
      {sublabel && <div className="dash-page-header__sublabel">{sublabel}</div>}
    </div>
  )
}
