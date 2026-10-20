import { Box, ButtonBase, Typography } from "@mui/material";
import VideoLibraryOutlined from "@mui/icons-material/VideoLibraryOutlined";
import GridViewRounded from "@mui/icons-material/GridViewRounded";
import AddRounded from "@mui/icons-material/AddRounded";
import TollOutlined from "@mui/icons-material/TollOutlined";
import SettingsOutlined from "@mui/icons-material/SettingsOutlined";

interface MobileNavigationProps {
  active: string;
  credits?: number;
  onLibrary: () => void;
  onTemplates: () => void;
  onCreate: () => void;
  onCredits: () => void;
  onSettings: () => void;
}

export function MobileNavigation(props: MobileNavigationProps) {
  const items = [
    {
      id: "library",
      label: "Library",
      icon: VideoLibraryOutlined,
      run: props.onLibrary,
    },
    {
      id: "templates",
      label: "Templates",
      icon: GridViewRounded,
      run: props.onTemplates,
    },
    { id: "create", label: "Create", icon: AddRounded, run: props.onCreate },
    {
      id: "credits",
      label: "Credits",
      icon: TollOutlined,
      run: props.onCredits,
    },
    {
      id: "settings",
      label: "Settings",
      icon: SettingsOutlined,
      run: props.onSettings,
    },
  ];
  return (
    <Box
      component="nav"
      aria-label="Mobile navigation"
      sx={{
        position: "fixed",
        bottom: 0,
        left: 0,
        right: 0,
        zIndex: 1200,
        bgcolor: "background.paper",
        borderTop: "1px solid",
        borderColor: "divider",
        boxShadow: "0 -6px 28px #00000008",
        pb: "env(safe-area-inset-bottom, 0px)",
        display: "grid",
        gridTemplateColumns: "repeat(5, minmax(0, 1fr))",
        height: "calc(72px + env(safe-area-inset-bottom, 0px))",
      }}
    >
      {items.map(({ id, label, icon: Icon, run }) => (
        <ButtonBase
          key={id}
          onClick={run}
          aria-current={props.active === id ? "page" : undefined}
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 0.5,
            overflow: "visible",
            color: props.active === id ? "primary.main" : "text.secondary",
            minWidth: 0,
          }}
        >
          <Box
            sx={
              id === "create"
                ? {
                    mt: "-25px",
                    width: 60,
                    height: 60,
                    borderRadius: "50%",
                    bgcolor: "primary.main",
                    color: "primary.contrastText",
                    display: "grid",
                    placeItems: "center",
                    border: "6px solid",
                    borderColor: "background.paper",
                    boxSizing: "content-box",
                    boxShadow: "0 5px 18px #cc3f3730",
                  }
                : { display: "grid", placeItems: "center", height: 27 }
            }
          >
            <Icon sx={{ fontSize: id === "create" ? 32 : 23 }} />
          </Box>
          <Typography
            component="span"
            sx={{
              fontSize: 11,
              fontWeight: props.active === id ? 750 : 550,
              lineHeight: 1.4,
            }}
          >
            {label}
          </Typography>
        </ButtonBase>
      ))}
    </Box>
  );
}
