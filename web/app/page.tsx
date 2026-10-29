import { CheckWorkspace } from "@/components/check-workspace";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

export default function Page() {
  return (
    <>
      <SiteHeader />
      <CheckWorkspace />
      <SiteFooter />
    </>
  );
}
