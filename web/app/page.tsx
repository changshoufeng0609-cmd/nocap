import { ReceiptsApp } from "@/components/receipts-app";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

export default function Page() {
  return (
    <>
      <SiteHeader />
      <ReceiptsApp />
      <SiteFooter />
    </>
  );
}
